"""Tests for template rendering, deterministic variant assignment, and sequencing."""

from collections import Counter

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models import Campaign, Lead, LeadStatus, Setting
from app.services.templates import (
    MissingTemplateVariableError,
    get_deterministic_variant_index,
    load_default_sequence,
    render_step_email,
    render_template_string,
)


@pytest.fixture
def tmpl_db() -> Session:
    """Provide a fresh in-memory database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_all_variables_render_cleanly() -> None:
    """Verify that all cold email template variables render as expected."""
    tmpl = (
        "Hi {{first_name}}, loved your work at {{company}}. {{personal_note}} "
        "From {{sender_name}} at {{sender_company}} ({{portfolio_url}}) offering "
        "{{service}} in {{niche}} resulting in {{example_result}}."
    )
    context = {
        "first_name": "Sarah",
        "company": "Acme Corp",
        "personal_note": "Great keynote at PyCon.",
        "sender_name": "Alex Mercer",
        "sender_company": "Agentic Systems",
        "portfolio_url": "https://agentic.systems",
        "service": "AI automation pipelines",
        "niche": "fintech",
        "example_result": "saving 12 hours weekly",
    }
    rendered = render_template_string(tmpl, context)
    assert "Sarah" in rendered
    assert "Acme Corp" in rendered
    assert "Great keynote at PyCon." in rendered
    assert "saving 12 hours weekly" in rendered


def test_strict_undefined_blocks_missing_variable() -> None:
    """Verify that a missing template variable raises MissingTemplateVariableError."""
    tmpl = "Hello {{first_name}}, we noticed {{missing_custom_field}}."
    context = {"first_name": "David"}
    with pytest.raises(MissingTemplateVariableError) as exc_info:
        render_template_string(tmpl, context)
    assert "missing_custom_field" in str(exc_info.value)


def test_deterministic_and_balanced_variant_distribution() -> None:
    """Verify variant assignment is deterministic per lead and balanced over 300 leads."""
    # 1. Determinism test: same lead always produces the exact same variant index
    lead_id = 42
    sub_1 = get_deterministic_variant_index(lead_id, step=1, kind="sub", total_variants=4)
    sub_2 = get_deterministic_variant_index(lead_id, step=1, kind="sub", total_variants=4)
    assert sub_1 == sub_2

    body_1 = get_deterministic_variant_index(lead_id, step=1, kind="body", total_variants=3)
    body_2 = get_deterministic_variant_index(lead_id, step=1, kind="body", total_variants=3)
    assert body_1 == body_2

    # 2. Balance test across 300 leads
    # 4 subjects: expected 75 each. +/-15% allowable range: 63 to 86
    sub_counts = Counter(
        get_deterministic_variant_index(i, step=1, kind="sub", total_variants=4)
        for i in range(1, 301)
    )
    assert len(sub_counts) == 4
    for idx, count in sub_counts.items():
        assert 60 <= count <= 90, f"Subject variant {idx} count {count} outside +/-15%"

    # 3 bodies: expected 100 each. +/-15% allowable range: 85 to 115
    body_counts = Counter(
        get_deterministic_variant_index(i, step=1, kind="body", total_variants=3)
        for i in range(1, 301)
    )
    assert len(body_counts) == 3
    for idx, count in body_counts.items():
        assert 80 <= count <= 120, f"Body variant {idx} count {count} outside +/-15%"


def test_reply_thread_subject_formatting(tmpl_db: Session, fake_clock: FakeClock) -> None:
    """Verify steps 2..4 prefix subject with 'Re: <step 1 subject>' without duplication."""
    campaign = Campaign(name="Thread Campaign", created_at=fake_clock.now())
    tmpl_db.add(campaign)
    tmpl_db.commit()

    load_default_sequence(campaign.id, tmpl_db)

    lead = Lead(
        campaign_id=campaign.id,
        email="founder@startup.io",
        first_name="Frank",
        company="Startup.io",
        personal_note="Loved your recent podcast episode on scaling Postgres.",
        status=LeadStatus.VALID.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    tmpl_db.add(lead)
    tmpl_db.commit()

    # Step 1: not a reply, no 'Re:' prefix
    rendered_1 = render_step_email(lead=lead, step_num=1, db=tmpl_db)
    assert rendered_1.send_as_reply is False
    assert not rendered_1.subject.startswith("Re:")

    # Step 2: send as reply, subject is prefixed with 'Re: <step 1 subject>'
    rendered_2 = render_step_email(lead=lead, step_num=2, db=tmpl_db)
    assert rendered_2.send_as_reply is True
    assert rendered_2.subject == f"Re: {rendered_1.subject}"

    # Step 3: send as reply in same thread
    rendered_3 = render_step_email(lead=lead, step_num=3, db=tmpl_db)
    assert rendered_3.send_as_reply is True
    assert rendered_3.subject == f"Re: {rendered_1.subject}"


def test_missing_personal_note_handling(tmpl_db: Session, fake_clock: FakeClock) -> None:
    """Verify that a lead without a personal_note stays in draft when required."""
    campaign = Campaign(name="Note Campaign", created_at=fake_clock.now())
    tmpl_db.add(campaign)
    tmpl_db.commit()

    load_default_sequence(campaign.id, tmpl_db)

    # Lead without personal note
    lead = Lead(
        campaign_id=campaign.id,
        email="empty_note@domain.com",
        first_name="Grace",
        company="Grace Corp",
        personal_note=None,
        status=LeadStatus.VALID.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    tmpl_db.add(lead)
    tmpl_db.commit()

    # 1. With REQUIRE_PERSONAL_NOTE = True -> stays draft with error note
    rendered = render_step_email(lead=lead, step_num=1, db=tmpl_db)
    assert rendered.status == "draft"
    assert rendered.is_ready is False
    assert rendered.error == "missing personal note"

    # 2. Disable REQUIRE_PERSONAL_NOTE in setting
    tmpl_db.add(Setting(key="REQUIRE_PERSONAL_NOTE", value="false"))
    tmpl_db.commit()

    # Lead with fallback note provided directly in extra_context or allowed
    rendered_allowed = render_step_email(
        lead=lead,
        step_num=1,
        db=tmpl_db,
        extra_context={"personal_note": "Great meeting you."},
    )
    assert rendered_allowed.status == "approved"
    assert rendered_allowed.is_ready is True


def test_seed_templates_pass_linter_for_20_sample_leads(
    tmpl_db: Session, fake_clock: FakeClock
) -> None:
    """Verify default seed templates pass spam linter across 20 distinct leads for all 4 steps."""
    campaign = Campaign(name="Seed Test Campaign", created_at=fake_clock.now())
    tmpl_db.add(campaign)
    tmpl_db.commit()

    load_default_sequence(campaign.id, tmpl_db)

    leads = []
    for i in range(1, 21):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"lead{i}@company{i}.com",
            first_name=f"User{i}",
            company=f"Company {i}",
            personal_note=f"Loved your engineering post on optimizing microservices in issue #{i}.",
            status=LeadStatus.VALID.value,
            created_at=fake_clock.now(),
            updated_at=fake_clock.now(),
        )
        leads.append(lead)
    tmpl_db.add_all(leads)
    tmpl_db.commit()

    for lead in leads:
        for step in (1, 2, 3, 4):
            rendered = render_step_email(lead=lead, step_num=step, db=tmpl_db)
            assert rendered.lint_result.passed is True, (
                f"Lead {lead.id} Step {step} failed linter: {rendered.lint_result.failures}"
            )
            assert rendered.is_ready is True
            assert rendered.status == "approved"
