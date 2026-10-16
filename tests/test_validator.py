"""Tests for email validation engine."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models import Campaign, DomainDnsCache, Event, EventType, Lead, LeadStatus, RiskLevel
from app.services.dns_resolver import FakeDnsResolver
from app.services.email_verifier import FakeEmailVerifier
from app.services.validator import evaluate_email, validate_leads


@pytest.fixture
def val_db() -> Session:
    """Provide a fresh in-memory database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_syntax_and_disposable_and_role_checks(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify syntax, disposable, and role address classifications."""
    dns = FakeDnsResolver()
    verifier = FakeEmailVerifier()

    # Malformed syntax
    risk, reason = evaluate_email("invalid-email", None, val_db, dns, verifier, fake_clock)
    assert risk == RiskLevel.BAD.value
    assert "syntax" in reason.lower()

    # Disposable domain
    risk, reason = evaluate_email("test@mailinator.com", None, val_db, dns, verifier, fake_clock)
    assert risk == RiskLevel.BAD.value
    assert "disposable" in reason.lower()

    # Typo domain (e.g. gail.com instead of gmail.com)
    risk, reason = evaluate_email("anwar@gail.com", None, val_db, dns, verifier, fake_clock)
    assert risk == RiskLevel.BAD.value
    assert "misspelled" in reason.lower() or "gmail.com" in reason.lower()

    # Role-based mailbox
    risk, reason = evaluate_email(
        "support@company.com", "https://company.com", val_db, dns, verifier, fake_clock
    )
    assert risk == RiskLevel.RISKY.value
    assert "role-based" in reason.lower()

    # Good email
    risk, reason = evaluate_email(
        "sarah@company.com", "https://company.com", val_db, dns, verifier, fake_clock
    )
    assert risk == RiskLevel.OK.value


def test_no_mx_domain(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify that domain without MX and without A records is marked bad."""
    dns = FakeDnsResolver()
    dns.set_domain("dead-domain.org", has_mx=False, has_a=False)
    verifier = FakeEmailVerifier()

    risk, reason = evaluate_email("user@dead-domain.org", None, val_db, dns, verifier, fake_clock)
    assert risk == RiskLevel.BAD.value
    assert "no mx or a records" in reason.lower()


def test_website_domain_mismatch_and_free_provider(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify that unrelated domain marks risky unless free provider."""
    dns = FakeDnsResolver()
    verifier = FakeEmailVerifier()

    # Mismatch with custom domain -> risky
    risk, reason = evaluate_email(
        "mark@acme.com", "https://othercorp.com", val_db, dns, verifier, fake_clock
    )
    assert risk == RiskLevel.RISKY.value
    assert "does not match website" in reason

    # Free email provider (e.g. gmail.com) -> ok even if website differs
    risk, reason = evaluate_email(
        "founder@gmail.com", "https://startup.io", val_db, dns, verifier, fake_clock
    )
    assert risk == RiskLevel.OK.value


def test_dns_cache_reuse(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify that DNS lookup results are cached in the database."""
    dns = FakeDnsResolver()
    verifier = FakeEmailVerifier()

    evaluate_email("alice@cached-domain.com", None, val_db, dns, verifier, fake_clock)

    cache_entries = val_db.scalars(select(DomainDnsCache)).all()
    assert len(cache_entries) == 1
    assert cache_entries[0].domain == "cached-domain.com"
    assert cache_entries[0].is_valid is True

    # Re-evaluate another user on same domain; ensure cache entry reused
    dns.set_domain("cached-domain.com", has_mx=False, has_a=False)  # even if resolver changed
    risk, _ = evaluate_email("bob@cached-domain.com", None, val_db, dns, verifier, fake_clock)
    assert risk == RiskLevel.OK.value  # cached valid state retained


def test_hunter_quota_exhaustion(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify external verifier stops after monthly quota is reached."""
    verifier = FakeEmailVerifier(monthly_quota=2)

    # First two calls use quota
    res1 = verifier.verify("user1@test.com", val_db, fake_clock)
    assert res1 is not None
    assert verifier.call_count == 1

    res2 = verifier.verify("user2@test.com", val_db, fake_clock)
    assert res2 is not None
    assert verifier.call_count == 2

    # Third call should hit quota and return None
    res3 = verifier.verify("user3@test.com", val_db, fake_clock)
    assert res3 is None
    assert verifier.call_count == 2


def test_allow_risky_flag_behavior(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify that ALLOW_RISKY controls whether risky leads become valid or invalid."""
    campaign = Campaign(name="Risk Campaign", created_at=fake_clock.now())
    val_db.add(campaign)
    val_db.commit()

    lead_held = Lead(
        campaign_id=campaign.id,
        email="info@testcompany.com",
        status=LeadStatus.NEW.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    val_db.add(lead_held)
    val_db.commit()

    dns = FakeDnsResolver()
    verifier = FakeEmailVerifier()

    # 1. ALLOW_RISKY = False -> lead becomes invalid
    summary = validate_leads(
        db=val_db,
        campaign_name="Risk Campaign",
        allow_risky=False,
        dns_resolver=dns,
        verifier=verifier,
        clock=fake_clock,
    )
    assert summary.valid_count == 0
    assert summary.invalid_count == 1
    assert summary.risk_risky == 1

    val_db.refresh(lead_held)
    assert lead_held.status == LeadStatus.INVALID.value
    assert lead_held.risk_level == RiskLevel.RISKY.value

    # 2. Reset to 'new' and test ALLOW_RISKY = True -> lead becomes valid
    lead_held.status = LeadStatus.NEW.value
    val_db.commit()

    summary_allowed = validate_leads(
        db=val_db,
        campaign_name="Risk Campaign",
        allow_risky=True,
        dns_resolver=dns,
        verifier=verifier,
        clock=fake_clock,
    )
    assert summary_allowed.valid_count == 1
    assert summary_allowed.invalid_count == 0
    val_db.refresh(lead_held)
    assert lead_held.status == LeadStatus.VALID.value

    # Verify audit event emitted
    events = val_db.scalars(select(Event).where(Event.type == EventType.VALIDATED.value)).all()
    assert len(events) == 2


def test_twelve_sample_emails_classification(val_db: Session, fake_clock: FakeClock) -> None:
    """Verify classification behavior across all 12 diverse sample email profiles."""
    dns = FakeDnsResolver()
    dns.set_domain("nomxdomain.test", has_mx=False, has_a=False)

    verifier = FakeEmailVerifier()
    verifier.set_result("ext_bad@domain.com", status="invalid", risk_level=RiskLevel.BAD.value)
    verifier.set_result(
        "ext_risky@domain.com", status="accept_all", risk_level=RiskLevel.RISKY.value
    )

    samples = [
        ("alice@techcorp.com", "https://techcorp.com", RiskLevel.OK.value),
        ("bob@gmail.com", "https://coolproject.io", RiskLevel.OK.value),
        ("bad-syntax-without-at", None, RiskLevel.BAD.value),
        ("space in@domain.com", None, RiskLevel.BAD.value),
        ("tempuser@mailinator.com", None, RiskLevel.BAD.value),
        ("disposable@10minutemail.com", None, RiskLevel.BAD.value),
        ("support@acme.com", "https://acme.com", RiskLevel.RISKY.value),
        ("info@venture.co", "https://venture.co", RiskLevel.RISKY.value),
        ("user@nomxdomain.test", None, RiskLevel.BAD.value),
        ("ceo@apple.com", "https://microsoft.com", RiskLevel.RISKY.value),
        ("ext_bad@domain.com", "https://domain.com", RiskLevel.BAD.value),
        ("ext_risky@domain.com", "https://domain.com", RiskLevel.RISKY.value),
    ]

    for email, website, expected_risk in samples:
        risk, reason = evaluate_email(email, website, val_db, dns, verifier, fake_clock)
        assert risk == expected_risk, f"Failed for {email}: got {risk}, expected {expected_risk}"
        assert reason is not None


def test_extract_domain_from_url_edge_cases() -> None:
    """Verify URL parsing handles edge cases."""
    from app.services.validator import extract_domain_from_url

    assert extract_domain_from_url(None) is None
    assert extract_domain_from_url("") is None
    assert extract_domain_from_url("acme.com") == "acme.com"
    assert extract_domain_from_url("http://www.acme.com:8080/path") == "acme.com"
    assert extract_domain_from_url("https://sub.domain.co.uk/") == "sub.domain.co.uk"


def test_effective_allow_risky_and_campaign_id_filter(val_db: Session) -> None:
    """Verify get_effective_allow_risky reads DB setting and validate_leads filters by campaign_id."""
    from app.models import Setting
    from app.services.validator import get_effective_allow_risky

    # Test default from settings
    assert get_effective_allow_risky(val_db) is False

    # Test setting in DB
    val_db.add(Setting(key="ALLOW_RISKY", value="true"))
    val_db.commit()
    assert get_effective_allow_risky(val_db) is True

    # Test validate_leads by campaign_id
    camp = Campaign(name="Filtered Camp")
    val_db.add(camp)
    val_db.commit()

    lead1 = Lead(campaign_id=camp.id, email="ok_user@acme.com", status=LeadStatus.NEW.value)
    lead2 = Lead(campaign_id=camp.id, email="invalid-format", status=LeadStatus.NEW.value)
    val_db.add_all([lead1, lead2])
    val_db.commit()

    dns = FakeDnsResolver()
    verifier = FakeEmailVerifier()
    clock = FakeClock()

    summary = validate_leads(
        val_db,
        campaign_id=camp.id,
        dns_resolver=dns,
        verifier=verifier,
        clock=clock,
    )
    assert summary.total_checked == 2
    assert summary.valid_count == 1
    assert summary.invalid_count == 1
