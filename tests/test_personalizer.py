"""Unit and scenario tests for Web Fetcher, LLM Client, Personalizer, and Approval Queue.

Tests:
1. Good personal note passes all verification and transitions lead to 'personalized'.
2. Hallucinated number is rejected and moves lead to 'needs_manual_note'.
3. Evidence quote not present in page text is rejected.
4. Generic flattery ('impressed', 'cutting-edge') is rejected.
5. LLM returning NONE is handled gracefully -> 'needs_manual_note'.
6. Thin page content (< 50 chars) is detected -> 'needs_manual_note'.
7. Broken / unreachable website is handled -> 'needs_manual_note'.
8. JavaScript-only placeholder page is detected -> 'needs_manual_note'.
9. Robots.txt disallow blocks scraping -> 'needs_manual_note'.
10. Web scraping database cache (30-day TTL) avoids repeat requests.
11. Daily LLM call cap (200) is strictly enforced.
12. CSV-provided personal_note is preserved and skips LLM.
13. Approval queue lifecycle: list_pending, approve, edit_and_approve, reject, regenerate.
14. Unapproved leads can NEVER be enrolled by the sequencer.
15. Scenario: 10 sample pages evaluation table.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.lead import Lead, LeadStatus
from app.models.setting import Setting
from app.services.approval import (
    approve,
    edit_and_approve,
    list_pending,
    regenerate,
    reject,
)
from app.services.fetcher import FakeFetcher, fetch_website
from app.services.llm import (
    FakeLlm,
    LlmDailyCapExceededError,
    check_and_increment_daily_cap,
)
from app.services.personalizer import (
    personalize_lead,
    verify_personal_note,
)
from app.services.sequencer import enroll


@pytest.fixture
def test_db() -> tuple[Session, sessionmaker]:
    """In-memory SQLite database session and factory."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sm() as session:
        campaign = Campaign(name="Personalization Campaign", status=CampaignStatus.ACTIVE.value)
        session.add(campaign)
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-10-01"))
        session.add(Setting(key="MAILBOX_TZ", value="UTC"))
        session.add(Setting(key="AUTO_APPROVE", value="false"))
        session.commit()
        yield session, sm


SAMPLE_HTML_PAGES = {
    "https://cloudbackup.io": (
        "<html><head><title>CloudBackup - Postgres Backups</title>"
        '<meta name="description" content="Automated PostgreSQL backups on AWS."></head>'
        "<body><h1>Automated Postgres Database Backup</h1>"
        "<p>CloudBackup automates daily snapshot verification and point-in-time recovery for "
        "PostgreSQL clusters. We help DevOps teams eliminate manual scripts and restore tables "
        "in under three minutes.</p></body></html>"
    ),
    "https://growthcraft.co": (
        "<html><head><title>GrowthCraft | B2B Shopify SEO</title></head>"
        "<body><h1>Conversion Optimization for Retailers</h1>"
        "<p>GrowthCraft helps high-growth apparel and wellness retailers scale "
        "organic search traffic and optimize checkout conversion funnels.</p></body></html>"
    ),
    "https://dermasync.com": (
        "<html><head><title>DermaSync - Telehealth for Clinics</title></head>"
        "<body><h1>Asynchronous Teledermatology Workflows</h1>"
        "<p>DermaSync provides HIPAA-compliant asynchronous photo consultation tools for private "
        "dermatology clinics across the Midwest.</p></body></html>"
    ),
    "https://fintechflow.com": (
        "<html><head><title>FintechFlow - Global Payouts</title></head>"
        "<body><h1>Global Treasury APIs</h1>"
        "<p>FintechFlow enables digital platforms to disburse developer payouts across Europe "
        "and Asia with local clearing rails.</p></body></html>"
    ),
    "https://logisticsbot.com": (
        "<html><head><title>LogisticsBot - Warehouse Dispatch</title></head>"
        "<body><h1>Automated Fleet Scheduling</h1>"
        "<p>LogisticsBot connects legacy ERP systems with autonomous mobile robots for "
        "manufacturing distribution centers.</p></body></html>"
    ),
    "https://creativestack.io": (
        "<html><head><title>CreativeStack - Design Tokens</title></head>"
        "<body><h1>Figma to Code Component Sync</h1>"
        "<p>CreativeStack synchronizes production React design tokens "
        "directly from Figma component libraries.</p></body></html>"
    ),
    "https://stealthstartup.com": (
        "<html><head><title>Coming Soon</title></head>"
        "<body><h1>Welcome</h1><p>Something new is coming soon.</p></body></html>"
    ),
    "https://spa-app.net": (
        "<html><head><title>App</title></head>"
        "<body><noscript>You need to enable JavaScript to run this app.</noscript>"
        '<div id="root"></div></body></html>'
    ),
    "https://privatecorp.com": (
        "<html><head><title>Private Corp</title></head>"
        "<body><h1>Enterprise Private Solutions</h1>"
        "<p>PrivateCorp offers sovereign cloud isolation for defense contractors.</p></body></html>"
    ),
    "https://megasite.org": (
        "<html><head><title>MegaSite Enterprise Hub</title></head>"
        "<body><h1>Enterprise Scale Architecture</h1><p>"
        + ("High-throughput distributed compute clusters for genomic sequence alignment. " * 50)
        + "</p></body></html>"
    ),
}


# =============================================================================
# 1. Verification Logic Unit Tests
# =============================================================================
def test_verify_personal_note_good_passes() -> None:
    """A factual, calm observation with exact evidence quote passes."""
    text = "CloudBackup automates daily snapshot verification for PostgreSQL clusters."
    note = "I saw that your platform automates daily snapshot verification for PostgreSQL."
    quote = "automates daily snapshot verification"

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is True
    assert reason is None


def test_verify_personal_note_hallucinated_number_rejected() -> None:
    """A note containing a number not found in scraped text is strictly rejected."""
    text = "FintechFlow enables digital platforms to disburse developer payouts across Europe."
    # '500' and 'million' are hallucinated numbers
    note = "I saw that your platform processes 500 million in developer payouts."
    quote = "disburse developer payouts"

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is False
    assert "hallucinated number" in reason.lower()


def test_verify_personal_note_missing_evidence_quote_rejected() -> None:
    """A quote not appearing literally in the text is strictly rejected."""
    text = "LogisticsBot connects legacy ERP systems with autonomous mobile robots."
    note = "I noticed your software connects legacy ERP systems with autonomous robots."
    quote = "award winning supply chain leader"  # Not in text

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is False
    assert "not found in scraped text" in reason


def test_verify_personal_note_generic_flattery_rejected() -> None:
    """Generic flattery words ('impressed', 'cutting-edge') are rejected."""
    text = "CreativeStack synchronizes production React design tokens directly from Figma."
    note = "I was impressed by your cutting-edge design token sync tool."
    quote = "design tokens"

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is False
    assert "generic flattery" in reason


def test_verify_personal_note_scraping_mention_rejected() -> None:
    """Explicitly mentioning scraping or looking at their website is rejected."""
    text = "DermaSync provides HIPAA-compliant asynchronous photo consultation tools."
    note = "While looking at your website, I saw your photo consultation tools."
    quote = "photo consultation tools"

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is False
    assert "scraping/monitoring" in reason


def test_verify_personal_note_word_count_cap() -> None:
    """Notes exceeding 25 words are rejected."""
    text = "We build custom data pipelines for logistics operators across North America."
    note = (
        "I noticed that your company specializes in building custom data pipelines "
        "for logistics operators across all of North America with reliable data feeds "
        "for enterprise supply chain distribution centers today."
    )  # 30 words
    quote = "custom data pipelines"

    ok, reason = verify_personal_note(note, quote, text)
    assert ok is False
    assert "exceeds 25 words" in reason


# =============================================================================
# 2. Pipeline Integration & Edge Cases
# =============================================================================
def test_good_lead_personalization(test_db: tuple[Session, sessionmaker]) -> None:
    """Rich website content generates a verified personal note and sets status to personalized."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="devops@cloudbackup.io",
        website="https://cloudbackup.io",
        company="CloudBackup",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "cloudbackup.io",
        '{"note": "I saw your platform automates daily snapshot verification.", '
        '"evidence_quote": "automates daily snapshot verification"}',
    )

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is True
    session.refresh(lead)

    assert lead.status == LeadStatus.PERSONALIZED.value
    assert "daily snapshot verification" in lead.personal_note
    assert lead.evidence_quote == "automates daily snapshot verification"


def test_llm_returns_none_marks_needs_manual_note(test_db: tuple[Session, sessionmaker]) -> None:
    """When LLM returns NONE due to insufficient facts, lead is marked needs_manual_note."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="info@cloudbackup.io",
        website="https://cloudbackup.io",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm(default_response="NONE")

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is False
    session.refresh(lead)

    assert lead.status == LeadStatus.NEEDS_MANUAL_NOTE.value
    assert "NONE" in lead.validation_reason


def test_thin_page_handling(test_db: tuple[Session, sessionmaker]) -> None:
    """Thin pages (< 50 chars visible text) skip the LLM and mark needs_manual_note."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="contact@stealthstartup.com",
        website="https://stealthstartup.com",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm()

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is False
    session.refresh(lead)

    assert lead.status == LeadStatus.NEEDS_MANUAL_NOTE.value
    assert llm.call_count == 0  # LLM must not be called on thin pages


def test_javascript_only_placeholder_handling(test_db: tuple[Session, sessionmaker]) -> None:
    """Pages with only JavaScript requirement notices are marked needs_manual_note."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="hello@spa-app.net",
        website="https://spa-app.net",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm()

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is False
    session.refresh(lead)

    assert lead.status == LeadStatus.NEEDS_MANUAL_NOTE.value
    assert "JavaScript" in lead.validation_reason


def test_broken_unreachable_page_handling(test_db: tuple[Session, sessionmaker]) -> None:
    """Unreachable sites (404/500/timeout) mark needs_manual_note."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="broken@brokencompany.org",
        website="https://brokencompany.org",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher()  # Has no registered pages -> returns 404
    llm = FakeLlm()

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is False
    session.refresh(lead)

    assert lead.status == LeadStatus.NEEDS_MANUAL_NOTE.value
    assert "404" in lead.validation_reason


def test_robots_txt_disallowed(test_db: tuple[Session, sessionmaker]) -> None:
    """Websites disallowed by robots.txt are skipped and marked needs_manual_note."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="sec@privatecorp.com",
        website="https://privatecorp.com",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    fetcher.disallow_domain("privatecorp.com")
    llm = FakeLlm()

    success, _ = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is False
    session.refresh(lead)

    assert lead.status == LeadStatus.NEEDS_MANUAL_NOTE.value
    assert "robots.txt" in lead.validation_reason


def test_30_day_caching(test_db: tuple[Session, sessionmaker]) -> None:
    """Scraped websites are cached in WebPageCache and reused within 30 days."""
    session, _ = test_db
    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    url = "https://growthcraft.co"

    # 1. First fetch scrapes page
    res1 = fetch_website(url, fetcher=fetcher, db=session, clock=clock)
    assert res1.is_usable is True
    assert res1.from_cache is False
    assert len(fetcher.fetched_urls) >= 1

    # Clear fetcher logs
    fetcher.fetched_urls.clear()

    # 2. Second fetch at day 10 uses cache
    clock.advance(timedelta(days=10))
    res2 = fetch_website(url, fetcher=fetcher, db=session, clock=clock)
    assert res2.is_usable is True
    assert res2.from_cache is True
    assert len(fetcher.fetched_urls) == 0  # No network call

    # 3. Third fetch after 31 days expires cache and refetches
    clock.advance(timedelta(days=21))  # Day 31
    res3 = fetch_website(url, fetcher=fetcher, db=session, clock=clock)
    assert res3.from_cache is False
    assert len(fetcher.fetched_urls) >= 1


def test_csv_provided_note_kept(test_db: tuple[Session, sessionmaker]) -> None:
    """If a lead already has a personal_note, it is preserved and skips LLM."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=campaign.id,
        email="founder@growthcraft.co",
        website="https://growthcraft.co",
        personal_note="Loved your talk on B2B Shopify conversion strategies.",
        status=LeadStatus.VALID.value,
    )
    session.add(lead)
    session.commit()

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm()

    success, msg = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
    assert success is True
    session.refresh(lead)

    assert lead.personal_note == "Loved your talk on B2B Shopify conversion strategies."
    assert lead.status == LeadStatus.PERSONALIZED.value
    assert llm.call_count == 0  # LLM skipped!


def test_daily_call_cap_enforced(test_db: tuple[Session, sessionmaker]) -> None:
    """Daily LLM call cap (200) halts calls when reached."""
    session, _ = test_db
    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)

    # Set daily calls to 200 in Setting
    session.add(Setting(key="LLM_CALLS_2026-10-15", value="200"))
    session.commit()

    with pytest.raises(LlmDailyCapExceededError, match="Daily LLM call cap reached"):
        check_and_increment_daily_cap(session, clock=clock, cap=200)


# =============================================================================
# 3. Approval Queue Lifecycle & Sequencer Gatekeeper
# =============================================================================
def test_approval_queue_lifecycle(test_db: tuple[Session, sessionmaker]) -> None:
    """Full lifecycle: list_pending, approve, edit_and_approve, reject, regenerate."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))
    clock = FakeClock(datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC))

    lead1 = Lead(
        campaign_id=campaign.id,
        email="lead1@test.com",
        website="https://cloudbackup.io",
        personal_note="I saw that your platform automates daily snapshot verification.",
        evidence_quote="automates daily snapshot verification",
        status=LeadStatus.PERSONALIZED.value,
    )
    lead2 = Lead(
        campaign_id=campaign.id,
        email="lead2@test.com",
        website="https://growthcraft.co",
        personal_note="I noticed your focus on checkout conversion optimization.",
        evidence_quote="checkout conversion optimization",
        status=LeadStatus.PERSONALIZED.value,
    )
    session.add_all([lead1, lead2])
    session.commit()

    # 1. list_pending
    pending = list_pending(campaign_id=campaign.id, db=session)
    assert len(pending) == 2

    # 2. approve lead1
    approved_lead1 = approve(lead1.id, db=session, clock=clock)
    assert approved_lead1.status == LeadStatus.APPROVED.value

    # 3. edit_and_approve lead2
    edited_lead2 = edit_and_approve(
        lead2.id,
        new_note="Saw your work scaling apparel brands on Shopify.",
        db=session,
        clock=clock,
    )
    assert edited_lead2.status == LeadStatus.APPROVED.value
    assert edited_lead2.personal_note == "Saw your work scaling apparel brands on Shopify."

    # Verify pending queue now empty
    assert len(list_pending(campaign_id=campaign.id, db=session)) == 0

    # 4. reject
    lead3 = Lead(
        campaign_id=campaign.id,
        email="lead3@test.com",
        personal_note="Bad draft",
        status=LeadStatus.PERSONALIZED.value,
    )
    session.add(lead3)
    session.commit()

    rejected_lead3 = reject(lead3.id, reason="Irrelevant observation", db=session, clock=clock)
    assert rejected_lead3.status == LeadStatus.NEEDS_MANUAL_NOTE.value

    # 5. regenerate
    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    llm = FakeLlm()
    llm.set_response(
        "cloudbackup.io",
        '{"note": "I saw your Postgres snapshot verification tools.", '
        '"evidence_quote": "snapshot verification"}',
    )
    lead1.website = "https://cloudbackup.io"
    regenerated = regenerate(lead1.id, db=session, fetcher=fetcher, llm=llm, clock=clock)
    assert regenerated.status == LeadStatus.PERSONALIZED.value
    assert "snapshot verification" in regenerated.personal_note


def test_unapproved_leads_can_never_be_enrolled(test_db: tuple[Session, sessionmaker]) -> None:
    """Sequencer enroll strictly ignores leads unless they are in 'approved' status."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    # Lead in 'personalized' status (draft not yet approved by human)
    lead_unapproved = Lead(
        campaign_id=campaign.id,
        email="unapproved@test.com",
        personal_note="Draft note waiting for review",
        status=LeadStatus.PERSONALIZED.value,
    )
    # Lead in 'valid' status
    lead_valid = Lead(
        campaign_id=campaign.id,
        email="valid@test.com",
        status=LeadStatus.VALID.value,
    )
    # Lead in 'approved' status
    lead_approved = Lead(
        campaign_id=campaign.id,
        email="approved@test.com",
        personal_note="I noticed your Postgres snapshot tools.",
        status=LeadStatus.APPROVED.value,
    )
    session.add_all([lead_unapproved, lead_valid, lead_approved])
    session.commit()

    enrolled = enroll(campaign, db=session)
    enrolled_emails = [ld.email for ld in enrolled]

    assert "approved@test.com" in enrolled_emails
    assert "unapproved@test.com" not in enrolled_emails
    assert "valid@test.com" not in enrolled_emails


# =============================================================================
# 4. Scenario: 10 Sample Pages Evaluation Table
# =============================================================================
def test_scenario_10_sample_pages_evaluation(test_db: tuple[Session, sessionmaker]) -> None:
    """Evaluate 10 sample pages and print audit summary table."""
    session, _ = test_db
    campaign = session.scalar(select(Campaign).limit(1))

    fetcher = FakeFetcher(SAMPLE_HTML_PAGES)
    fetcher.disallow_domain("privatecorp.com")  # Robots.txt disallowed

    llm = FakeLlm()
    # Script responses
    llm.set_response(
        "cloudbackup.io",
        '{"note": "I saw your platform automates daily snapshot verification.", '
        '"evidence_quote": "automates daily snapshot verification"}',
    )
    llm.set_response(
        "growthcraft.co",
        '{"note": "I noticed your team specializes in conversion optimization.", '
        '"evidence_quote": "apparel and wellness retailers"}',
    )
    llm.set_response(
        "dermasync.com",
        '{"note": "I saw your asynchronous photo consultation tools.", '
        '"evidence_quote": "asynchronous photo consultation tools"}',
    )
    llm.set_response(
        "fintechflow.com",
        '{"note": "I saw that your platform processes over 500 million dollars in payouts.", '
        '"evidence_quote": "disburse developer payouts"}',  # Hallucinated number!
    )
    llm.set_response(
        "logisticsbot.com",
        '{"note": "I noticed your software connects legacy ERP systems with mobile robots.", '
        '"evidence_quote": "award winning supply chain leader"}',  # Quote not in text!
    )
    llm.set_response(
        "creativestack.io",
        '{"note": "I was impressed by your cutting-edge design token sync tool.", '
        '"evidence_quote": "design tokens"}',  # Banned flattery!
    )
    llm.set_response(
        "megasite.org",
        '{"note": "I saw your distributed compute clusters for genomic sequence alignment.", '
        '"evidence_quote": "distributed compute clusters for genomic sequence alignment"}',
    )

    test_cases = [
        ("CloudBackup (Rich SaaS)", "https://cloudbackup.io", "PASS"),
        ("GrowthCraft (Rich Agency)", "https://growthcraft.co", "PASS"),
        ("DermaSync (Rich Health)", "https://dermasync.com", "PASS"),
        (
            "FintechFlow (Hallucinated No.)",
            "https://fintechflow.com",
            "REJECTED (hallucinated number)",
        ),
        ("LogisticsBot (Bad Quote)", "https://logisticsbot.com", "REJECTED (quote not in text)"),
        ("CreativeStack (Flattery)", "https://creativestack.io", "REJECTED (generic flattery)"),
        ("StealthStartup (Thin <50ch)", "https://stealthstartup.com", "REJECTED (thin content)"),
        ("BrokenCompany (404/Down)", "https://brokencompany.org", "REJECTED (unreachable 404)"),
        ("SpaApp (JavaScript-only)", "https://spa-app.net", "REJECTED (JS placeholder)"),
        ("PrivateCorp (Robots Block)", "https://privatecorp.com", "REJECTED (robots.txt disallow)"),
    ]

    results_table = []
    for idx, (label, url, _expected_verdict) in enumerate(test_cases, 1):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"lead_{idx}@{url.split('//')[-1]}",
            website=url,
            status=LeadStatus.VALID.value,
        )
        session.add(lead)
        session.flush()

        success, summary = personalize_lead(lead, db=session, fetcher=fetcher, llm=llm)
        session.refresh(lead)

        results_table.append(
            {
                "index": idx,
                "label": label,
                "url": url,
                "note": lead.personal_note or "NONE",
                "evidence": lead.evidence_quote or "N/A",
                "status": lead.status,
                "verdict": "PASS" if success else "REJECTED",
                "reason": lead.validation_reason or "Verified",
            }
        )

    # Print markdown table for report
    print("\n" + "=" * 110)
    print("10 SAMPLE PAGES EVALUATION TABLE")
    print("=" * 110)
    header = (
        f"{'#':<3} | {'Page & Profile':<30} | {'Status':<18} | "
        f"{'Generated Personal Note / Rejection Reason':<50}"
    )
    print(header)
    print("-" * 110)
    for r in results_table:
        note_or_reason = r["note"] if r["verdict"] == "PASS" else r["reason"]
        print(f"{r['index']:<3} | {r['label']:<30} | {r['status']:<18} | {note_or_reason[:50]}")
    print("=" * 110 + "\n")

    # Assert expected outcomes
    assert results_table[0]["verdict"] == "PASS"
    assert results_table[1]["verdict"] == "PASS"
    assert results_table[2]["verdict"] == "PASS"
    assert results_table[3]["verdict"] == "REJECTED"
    assert results_table[4]["verdict"] == "REJECTED"
    assert results_table[5]["verdict"] == "REJECTED"
    assert results_table[6]["verdict"] == "REJECTED"
    assert results_table[7]["verdict"] == "REJECTED"
    assert results_table[8]["verdict"] == "REJECTED"
    assert results_table[9]["verdict"] == "REJECTED"
