"""360-degree scenario and integration tests for the Master Personalizer Engine.

Validates that:
1. Missing Website -> Automatically pitched 'website' (modern web apps & landing pages).
2. Weak SEO Website -> Pitched 'seo' (technical SEO & search visibility pipelines).
3. Repetitive Operations -> Pitched 'automation' (bespoke AI workflow pipelines).
4. Tech / SaaS Platform -> Pitched 'custom_software' (custom software & API infrastructure).
5. All 4 cold sequence email steps render dynamic {{service}}, {{example_result}}, and {{personal_note}} cleanly.
6. Partial LLM outputs fallback safely without broken templates.
7. Campaign-level batch personalization successfully handles mixed leads.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.lead import Lead, LeadStatus
from app.models.setting import Setting
from app.services.fetcher import FakeFetcher
from app.services.llm import FakeLlm
from app.services.personalizer import (
    personalize_campaign,
    personalize_lead,
)
from app.services.templates import (
    load_default_sequence,
    render_step_email,
)

SAMPLE_PAGES = {
    "https://dallasroofing.com": (
        "<html><head><title>Dallas Roofing Experts</title></head>"
        "<body><h1>Premier Residential Roofing Services</h1>"
        "<p>Our family business provides residential roofing across North Texas with "
        "comprehensive emergency storm damage inspections.</p></body></html>"
    ),
    "https://fleetdispatch.net": (
        "<html><head><title>FleetDispatch - Logistics</title></head>"
        "<body><h1>Automated Fleet Scheduling</h1>"
        "<p>FleetDispatch connects warehouse inventory with regional freight dispatchers "
        "to coordinate truck routes across five distribution hubs.</p></body></html>"
    ),
    "https://authgateway.io": (
        "<html><head><title>AuthGateway - Developer Authentication</title></head>"
        "<body><h1>High-Throughput Identity Infrastructure</h1>"
        "<p>AuthGateway provides developer-first SAML and OAuth microservices "
        "for B2B multi-tenant enterprise software platforms.</p></body></html>"
    ),
}


@pytest.fixture
def master_db() -> tuple[Session, Campaign]:
    """In-memory database setup with active campaign and default sequence loaded."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sm() as session:
        campaign = Campaign(
            name="Master Outreach Campaign",
            niche="commercial services",
            status=CampaignStatus.ACTIVE.value,
        )
        session.add(campaign)
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-10-01"))
        session.add(Setting(key="MAILBOX_TZ", value="UTC"))
        session.add(Setting(key="AUTO_APPROVE", value="false"))
        session.commit()
        session.refresh(campaign)

        # Load standard 4-step sequence
        load_default_sequence(campaign.id, session)

        yield session, campaign


def test_missing_website_pitches_website(master_db: tuple[Session, Campaign]) -> None:
    """Lead without a website is automatically personalized with 'website' pitch angle."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="contact@apexplumbingdallas.com",
        first_name="Marcus",
        company="Apex Plumbing",
        website=None,  # NO WEBSITE
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "Apex Plumbing",
        '{"pitch_angle": "website", '
        '"offered_service": "modern, high-performing websites and web applications", '
        '"example_result": "turning digital visitors into qualified inbound leads consistently", '
        '"note": "I saw that Apex Plumbing provides commercial maintenance across Dallas.", '
        '"evidence_quote": "Apex Plumbing"}',
    )

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert success is True
    session.refresh(lead)

    assert lead.status == LeadStatus.PERSONALIZED.value
    assert lead.pitch_angle == "website"
    assert lead.offered_service == "modern, high-performing websites and web applications"
    assert (
        lead.example_result == "turning digital visitors into qualified inbound leads consistently"
    )
    assert "Apex Plumbing provides commercial maintenance" in lead.personal_note

    # Verify Email Sequence Step 1 rendering
    rendered_1 = render_step_email(lead=lead, step_num=1, db=session)
    assert rendered_1.is_ready is True
    assert rendered_1.status == "approved"
    assert "modern, high-performing websites and web applications" in rendered_1.body
    assert "turning digital visitors into qualified inbound leads consistently" in rendered_1.body
    assert lead.personal_note in rendered_1.body
    assert rendered_1.lint_result.passed is True


def test_weak_seo_website_pitches_seo(master_db: tuple[Session, Campaign]) -> None:
    """Lead with thin SEO presence is personalized with 'seo' pitch angle."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="owner@dallasroofing.com",
        first_name="David",
        company="Dallas Roofing",
        website="https://dallasroofing.com",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "dallasroofing.com",
        '{"pitch_angle": "seo", '
        '"offered_service": "technical SEO and organic search visibility pipelines", '
        '"example_result": "a 2.5x increase in qualified organic search traffic within 90 days", '
        '"note": "I noticed your team offers residential roofing across North Texas.", '
        '"evidence_quote": "residential roofing across North Texas"}',
    )

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert success is True
    session.refresh(lead)

    assert lead.status == LeadStatus.PERSONALIZED.value
    assert lead.pitch_angle == "seo"
    assert lead.offered_service == "technical SEO and organic search visibility pipelines"
    assert "2.5x increase in qualified organic search traffic" in lead.example_result

    # Verify Email Step 1 rendering
    rendered_1 = render_step_email(lead=lead, step_num=1, db=session)
    assert rendered_1.is_ready is True
    assert "technical SEO and organic search visibility pipelines" in rendered_1.body
    assert "a 2.5x increase in qualified organic search traffic" in rendered_1.body


def test_operational_business_pitches_automation(master_db: tuple[Session, Campaign]) -> None:
    """Operational business is personalized with 'automation' pitch angle."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="ops@fleetdispatch.net",
        first_name="Elena",
        company="FleetDispatch",
        website="https://fleetdispatch.net",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "fleetdispatch.net",
        '{"pitch_angle": "automation", '
        '"offered_service": "bespoke AI workflow automation pipelines", '
        '"example_result": "reclaiming 15+ engineering and operational hours weekly", '
        '"note": "I saw your system coordinates truck routes across distribution hubs.", '
        '"evidence_quote": "coordinate truck routes across five distribution hubs"}',
    )

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert success is True
    session.refresh(lead)

    assert lead.pitch_angle == "automation"
    assert lead.offered_service == "bespoke AI workflow automation pipelines"

    # Verify Step 2 follow-up email rendering (with thread Re: subject)
    rendered_1 = render_step_email(lead=lead, step_num=1, db=session)
    rendered_2 = render_step_email(lead=lead, step_num=2, db=session)
    assert rendered_2.send_as_reply is True
    assert rendered_2.subject == f"Re: {rendered_1.subject}"
    assert "bespoke AI workflow automation pipelines" in rendered_2.body


def test_tech_saas_platform_pitches_custom_software(master_db: tuple[Session, Campaign]) -> None:
    """Developer/tech platform is personalized with 'custom_software' pitch angle."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="cto@authgateway.io",
        first_name="Alex",
        company="AuthGateway",
        website="https://authgateway.io",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "authgateway.io",
        '{"pitch_angle": "custom_software", '
        '"offered_service": "custom software development and cloud API infrastructure", '
        '"example_result": "shipping core product features 3x faster with zero technical debt", '
        '"note": "I saw your platform provides developer-first identity microservices.", '
        '"evidence_quote": "developer-first SAML and OAuth microservices"}',
    )

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert success is True
    session.refresh(lead)

    assert lead.pitch_angle == "custom_software"
    assert lead.offered_service == "custom software development and cloud API infrastructure"

    # Verify all 4 sequence steps render without error
    for step_num in (1, 2, 3, 4):
        rendered = render_step_email(lead=lead, step_num=step_num, db=session)
        assert rendered.is_ready is True
        assert rendered.lint_result.passed is True


def test_partial_llm_json_uses_safe_fallbacks(master_db: tuple[Session, Campaign]) -> None:
    """If LLM response omits offered_service or example_result, safe fallbacks apply."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="founder@nowebsitehere.org",
        first_name="Jordan",
        company="Nowebsite Corp",
        website=None,
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher()
    llm = FakeLlm()
    # Missing offered_service and example_result in JSON
    llm.set_response(
        "Nowebsite Corp",
        '{"pitch_angle": "website", '
        '"note": "I noticed Nowebsite Corp operates across the energy sector.", '
        '"evidence_quote": "Nowebsite Corp"}',
    )

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert success is True
    session.refresh(lead)

    # Defaults for 'website' angle must be applied
    assert lead.pitch_angle == "website"
    assert lead.offered_service == "modern, high-performing websites and web applications"
    assert (
        lead.example_result == "turning digital visitors into qualified inbound leads consistently"
    )

    rendered = render_step_email(lead=lead, step_num=1, db=session)
    assert rendered.is_ready is True
    assert "modern, high-performing websites and web applications" in rendered.body


def test_campaign_bulk_personalization(master_db: tuple[Session, Campaign]) -> None:
    """personalize_campaign accurately personalizes a mix of leads with different angles."""
    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    leads = [
        Lead(
            campaign_id=campaign.id,
            email="lead_nowebsite@example.com",
            company="Local Clinic",
            website=None,
            status=LeadStatus.VALID.value,
        ),
        Lead(
            campaign_id=campaign.id,
            email="lead_seo@dallasroofing.com",
            company="Dallas Roofing",
            website="https://dallasroofing.com",
            status=LeadStatus.VALID.value,
        ),
        Lead(
            campaign_id=campaign.id,
            email="lead_auto@fleetdispatch.net",
            company="FleetDispatch",
            website="https://fleetdispatch.net",
            status=LeadStatus.VALID.value,
        ),
    ]
    session.add_all(leads)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "Local Clinic",
        '{"pitch_angle": "website", '
        '"offered_service": "modern, high-performing websites and web applications", '
        '"example_result": "turning digital visitors into qualified inbound leads consistently", '
        '"note": "I noticed Local Clinic provides medical outpatient care in Ohio.", '
        '"evidence_quote": "Local Clinic"}',
    )
    llm.set_response(
        "dallasroofing.com",
        '{"pitch_angle": "seo", '
        '"offered_service": "technical SEO and organic search visibility pipelines", '
        '"example_result": "a 2.5x increase in qualified organic search traffic within 90 days", '
        '"note": "I noticed your team offers residential roofing across North Texas.", '
        '"evidence_quote": "residential roofing across North Texas"}',
    )
    llm.set_response(
        "fleetdispatch.net",
        '{"pitch_angle": "automation", '
        '"offered_service": "bespoke AI workflow automation pipelines", '
        '"example_result": "reclaiming 15+ engineering and operational hours weekly", '
        '"note": "I saw your system coordinates truck routes across distribution hubs.", '
        '"evidence_quote": "coordinate truck routes across five distribution hubs"}',
    )

    stats = personalize_campaign(
        campaign_name_or_id=campaign.id,
        db=session,
        fetcher=fetcher,
        llm=llm,
        clock=clock,
    )

    assert stats["total"] == 3
    assert stats["personalized"] == 3
    assert stats["needs_manual_note"] == 0

    # Ensure all leads have their distinct pitch angles
    reloaded_leads = session.query(Lead).filter(Lead.campaign_id == campaign.id).all()
    angles = {ld.email: ld.pitch_angle for ld in reloaded_leads}
    assert angles["lead_nowebsite@example.com"] == "website"
    assert angles["lead_seo@dallasroofing.com"] == "seo"
    assert angles["lead_auto@fleetdispatch.net"] == "automation"


def test_gemini_client_unconfigured_operates_free_mode(master_db: tuple[Session, Campaign]) -> None:
    """When GEMINI_API_KEY is not configured, GeminiClient runs in smart free mode without crashing."""
    from app.core.config import Settings
    from app.services.llm import GeminiClient

    session, campaign = master_db
    clock = FakeClock(datetime(2026, 10, 16, 10, 0, 0, tzinfo=UTC))

    lead = Lead(
        campaign_id=campaign.id,
        email="owner@freemode.com",
        first_name="Sam",
        company="Swift Plumbing Services",
        website=None,  # No website
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    # Gemini client with empty / placeholder key
    unconfigured_settings = Settings(GEMINI_API_KEY=None, LLM_PROVIDER="gemini")
    gemini = GeminiClient(settings=unconfigured_settings)
    fetcher = FakeFetcher(SAMPLE_PAGES)

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=gemini, clock=clock)
    assert success is True
    session.refresh(lead)

    assert lead.status == LeadStatus.PERSONALIZED.value
    assert lead.pitch_angle == "website"
    assert lead.offered_service == "modern, high-performing websites and web applications"
    assert "Swift Plumbing Services" in lead.personal_note

    rendered = render_step_email(lead=lead, step_num=1, db=session)
    assert rendered.is_ready is True
    assert rendered.lint_result.passed is True
