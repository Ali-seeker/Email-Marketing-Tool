"""Tests for suppression list service and message cancellation."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models import (
    Campaign,
    EmailMessage,
    EmailMessageStatus,
    Event,
    EventType,
    Lead,
    LeadStatus,
    Suppression,
)
from app.services.suppression import (
    add,
    export_to_csv,
    import_from_csv,
    is_suppressed,
    remove,
)


@pytest.fixture
def supp_db() -> Session:
    """Provide a fresh in-memory database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_add_suppression_and_cancel_pending_messages(
    supp_db: Session, fake_clock: FakeClock
) -> None:
    """Verify suppressing an email flips lead to unsubscribed and cancels pending messages."""
    campaign = Campaign(name="Outreach 1", created_at=fake_clock.now())
    supp_db.add(campaign)
    supp_db.commit()

    lead = Lead(
        campaign_id=campaign.id,
        email="target@example.com",
        status=LeadStatus.IN_SEQUENCE.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    supp_db.add(lead)
    supp_db.commit()

    # Create one draft message, one scheduled message, and one already sent message
    msg_draft = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Intro",
        body="Hello",
        status=EmailMessageStatus.DRAFT.value,
    )
    msg_sched = EmailMessage(
        lead_id=lead.id,
        step=2,
        subject="Follow-up",
        body="Checking in",
        status=EmailMessageStatus.SCHEDULED.value,
    )
    msg_sent = EmailMessage(
        lead_id=lead.id,
        step=0,
        subject="Previous",
        body="Old email",
        status=EmailMessageStatus.SENT.value,
    )
    supp_db.add_all([msg_draft, msg_sched, msg_sent])
    supp_db.commit()

    # Suppress target email
    add(db=supp_db, target="target@example.com", reason="opt_out", clock=fake_clock)

    # Verify suppression record exists
    assert is_suppressed(supp_db, "target@example.com") is True
    assert is_suppressed(supp_db, "other@example.com") is False

    # Verify lead flipped to unsubscribed
    supp_db.refresh(lead)
    assert lead.status == LeadStatus.UNSUBSCRIBED.value

    # Verify pending messages are skipped
    supp_db.refresh(msg_draft)
    supp_db.refresh(msg_sched)
    supp_db.refresh(msg_sent)
    assert msg_draft.status == EmailMessageStatus.SKIPPED.value
    assert "suppressed" in (msg_draft.error or "").lower()
    assert msg_sched.status == EmailMessageStatus.SKIPPED.value
    # Sent message remains sent
    assert msg_sent.status == EmailMessageStatus.SENT.value

    # Verify event logged
    events = supp_db.scalars(select(Event).where(Event.type == EventType.UNSUBSCRIBED.value)).all()
    assert len(events) == 1


def test_suppression_bounce_reason_flips_to_bounced(
    supp_db: Session, fake_clock: FakeClock
) -> None:
    """Verify suppression with bounce reason flips lead status to bounced."""
    campaign = Campaign(name="Outreach 2", created_at=fake_clock.now())
    supp_db.add(campaign)
    supp_db.commit()

    lead = Lead(
        campaign_id=campaign.id,
        email="bounced@domain.com",
        status=LeadStatus.IN_SEQUENCE.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    supp_db.add(lead)
    supp_db.commit()

    add(db=supp_db, target="bounced@domain.com", reason="bounce", clock=fake_clock)

    supp_db.refresh(lead)
    assert lead.status == LeadStatus.BOUNCED.value

    events = supp_db.scalars(select(Event).where(Event.type == EventType.BOUNCED.value)).all()
    assert len(events) == 1


def test_domain_level_suppression(supp_db: Session, fake_clock: FakeClock) -> None:
    """Verify domain-level suppression blocks all addresses under that domain."""
    campaign = Campaign(name="Competitor Outreach", created_at=fake_clock.now())
    supp_db.add(campaign)
    supp_db.commit()

    lead1 = Lead(
        campaign_id=campaign.id,
        email="ceo@rival.com",
        status=LeadStatus.NEW.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    lead2 = Lead(
        campaign_id=campaign.id,
        email="sales@rival.com",
        status=LeadStatus.NEW.value,
        created_at=fake_clock.now(),
        updated_at=fake_clock.now(),
    )
    supp_db.add_all([lead1, lead2])
    supp_db.commit()

    # Add domain suppression
    add(db=supp_db, target="rival.com", reason="competitor", is_domain=True, clock=fake_clock)

    # Check is_suppressed on any email under that domain
    assert is_suppressed(supp_db, "ceo@rival.com") is True
    assert is_suppressed(supp_db, "random@rival.com") is True
    assert is_suppressed(supp_db, "user@other.com") is False

    # Matching leads flipped
    supp_db.refresh(lead1)
    supp_db.refresh(lead2)
    assert lead1.status == LeadStatus.UNSUBSCRIBED.value
    assert lead2.status == LeadStatus.UNSUBSCRIBED.value


def test_remove_suppression(supp_db: Session, fake_clock: FakeClock) -> None:
    """Verify removing an email or domain from suppression."""
    add(db=supp_db, target="unblock@test.com", reason="test", clock=fake_clock)
    assert is_suppressed(supp_db, "unblock@test.com") is True

    removed = remove(supp_db, "unblock@test.com")
    assert removed is True
    assert is_suppressed(supp_db, "unblock@test.com") is False

    # Removing non-existent returns False
    assert remove(supp_db, "ghost@test.com") is False


def test_csv_import_export_round_trip(
    supp_db: Session, fake_clock: FakeClock, tmp_path: Path
) -> None:
    """Verify suppression export and import round trip."""
    add(db=supp_db, target="a@sample.com", reason="optout", clock=fake_clock)
    add(db=supp_db, target="b@sample.com", reason="bounce", clock=fake_clock)
    add(db=supp_db, target="blocked.org", reason="competitor", is_domain=True, clock=fake_clock)

    export_file = tmp_path / "suppressions_export.csv"
    count = export_to_csv(supp_db, export_file)
    assert count == 3
    assert export_file.exists()

    # Clear table
    for s in supp_db.scalars(select(Suppression)).all():
        supp_db.delete(s)
    supp_db.commit()
    assert len(supp_db.scalars(select(Suppression)).all()) == 0

    # Import back from CSV
    imported_count = import_from_csv(supp_db, export_file, clock=fake_clock)
    assert imported_count == 3
    assert is_suppressed(supp_db, "a@sample.com") is True
    assert is_suppressed(supp_db, "b@sample.com") is True
    assert is_suppressed(supp_db, "any@blocked.org") is True
