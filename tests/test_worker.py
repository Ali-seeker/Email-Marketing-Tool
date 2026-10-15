"""Unit tests for the ColdReach outbound worker loop.

Covers:
- Strict socket-free guarantee: asserts no socket is opened in dry run across 30 emails.
- Pre-send checks: daily cap enforcement, minimum gap enforcement, suppression re-checking.
- Atomic row claiming: prevents race conditions between competing workers.
- Retry with exponential backoff: 10m, 30m, and failure after 2 retries.
- Permanent error: bounces lead and logs Event.
- Auth error & suspension detection: triggers emergency kill-switch and halts worker loop.
- Crash recovery: recovers stuck 'sending' messages (>10m) to 'needs_review' on startup.
"""

import socket
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.setting import Setting
from app.models.suppression import Suppression
from app.services.sender import DryRunSender, FakeSender
from app.worker import Worker, is_killswitch_active, recover_stuck_sending_messages


@pytest.fixture
def worker_db() -> tuple[Session, sessionmaker]:
    """Create in-memory SQLite database session and factory."""
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with session_factory() as session:
        # Create active default campaign
        campaign = Campaign(name="Worker Campaign", status=CampaignStatus.ACTIVE.value)
        session.add(campaign)

        # Settings
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-11-01"))
        session.add(Setting(key="LIVE_MODE", value="false"))
        session.add(Setting(key="MAILBOX_TZ", value="America/New_York"))
        session.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
        session.add(Setting(key="MIN_GAP_SECONDS", value="120"))
        session.add(Setting(key="SEND_WINDOW_START", value="00:00"))
        session.add(Setting(key="SEND_WINDOW_END", value="23:59"))
        session.add(Setting(key="SEND_DAYS", value='["Mon", "Tue", "Wed", "Thu", "Fri"]'))
        session.commit()
        yield session, session_factory


VALID_STEP_1_BODY = (
    "Hi Lead,\n\n"
    "I came across your work at Acme and wanted to reach out directly. "
    "Our team builds custom automation workflows for engineering companies. "
    "We recently helped another software company reclaim over fifteen engineering hours weekly "
    "by replacing fragmented scripts with reliable background workers. "
    "Would you have 15 minutes next Tuesday for a brief introductory conversation?\n\n"
    "Best regards,\nAlex Mercer\n\n"
    "P.S. If you are not interested, reply and I will not contact you again."
)


def _seed_lead_and_scheduled_msg(
    db: Session,
    campaign_id: int,
    email: str,
    scheduled_at: datetime,
    step: int = 1,
    status: str = LeadStatus.APPROVED.value,
) -> tuple[Lead, EmailMessage]:
    """Helper to seed lead and scheduled email message."""
    lead = Lead(
        campaign_id=campaign_id,
        email=email,
        first_name="Lead",
        last_name="Target",
        company="Acme",
        timezone="America/New_York",
        status=status,
        personal_note="Great work scaling the infra",
    )
    db.add(lead)
    db.flush()

    msg = EmailMessage(
        lead_id=lead.id,
        step=step,
        subject="Quick Question",
        body=VALID_STEP_1_BODY,
        status=EmailMessageStatus.SCHEDULED.value,
        scheduled_at=scheduled_at,
    )
    db.add(msg)
    db.commit()
    return lead, msg


def test_no_socket_opened_in_dry_run_across_30_emails(
    worker_db: tuple[Session, sessionmaker],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify that NO socket is opened while LIVE_MODE=false across a full run of 30 emails."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    start_time = clock.now()

    # Seed 30 scheduled emails due at or before now
    for i in range(30):
        _seed_lead_and_scheduled_msg(
            db,
            campaign.id,
            f"user_{i}@nosocket.com",
            scheduled_at=start_time - timedelta(minutes=30 - i),
        )

    # Patch socket.socket
    monkeypatch.setenv("LIVE_MODE", "false")
    from app.core.config import get_settings
    get_settings.cache_clear()
    socket_mock = MagicMock()
    monkeypatch.setattr(socket, "socket", socket_mock)

    # Use DryRunSender
    dry_sender = DryRunSender(db=db, outbox_dir=tmp_path / "outbox")
    worker = Worker(
        poll_interval=1.0,
        sender=dry_sender,
        clock=clock,
        session_factory=factory,
    )

    # Disable min gap temporarily to test all 30 emails in one run
    gap_setting = db.get(Setting, "MIN_GAP_SECONDS")
    if gap_setting:
        gap_setting.value = "0"
    db.add(Setting(key="IGNORE_RAMP_UP", value="true"))
    db.commit()

    sent_count = worker.run_once()

    assert sent_count == 30
    # Socket was NEVER called!
    assert not socket_mock.called

    # All 30 emails transitioned to sent (dry-run)
    stmt = select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SENT_DRY_RUN.value)
    dispatched = db.scalars(stmt).all()
    assert len(dispatched) == 30


def test_worker_daily_cap_enforcement(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify worker halts dispatches once today's capacity is exhausted."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    now = clock.now()

    # Set hard cap of 2 emails for test
    hard_max_setting = db.get(Setting, "HARD_MAX_PER_DAY")
    assert hard_max_setting is not None
    hard_max_setting.value = "2"
    gap_setting = db.get(Setting, "MIN_GAP_SECONDS")
    assert gap_setting is not None
    gap_setting.value = "0"
    db.commit()

    # Seed 5 scheduled emails
    for i in range(5):
        _seed_lead_and_scheduled_msg(
            db,
            campaign.id,
            f"cap_user_{i}@test.com",
            scheduled_at=now - timedelta(minutes=5),
        )

    sender = FakeSender()
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    sent = worker.run_once()
    assert sent == 2  # exactly capped at 2!

    # Remaining 3 stay scheduled
    remaining_scheduled = db.scalars(
        select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
    ).all()
    assert len(remaining_scheduled) == 3


def test_worker_gap_enforcement(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify worker respects MIN_GAP_SECONDS between consecutive sends."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    now = clock.now()

    # Gap is 120s
    _seed_lead_and_scheduled_msg(
        db, campaign.id, "gap1@test.com", scheduled_at=now - timedelta(minutes=5)
    )
    _seed_lead_and_scheduled_msg(
        db, campaign.id, "gap2@test.com", scheduled_at=now - timedelta(minutes=5)
    )

    sender = FakeSender()
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    # First run dispatches email 1
    sent_1 = worker.run_once()
    assert sent_1 == 1

    # Immediate second run at same time: blocked by minimum gap (120s)
    sent_2 = worker.run_once()
    assert sent_2 == 0

    # Advance clock by 60 seconds (total 60s < 120s): still blocked
    clock.advance(timedelta(seconds=60))
    sent_3 = worker.run_once()
    assert sent_3 == 0

    # Advance clock by another 61 seconds (total 121s > 120s): dispatches email 2!
    clock.advance(timedelta(seconds=61))
    sent_4 = worker.run_once()
    assert sent_4 == 1


def test_worker_retry_transient_error_with_backoff(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify transient errors retry with backoff of 10m then 30m, then fail permanently."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    now = clock.now()

    _, msg = _seed_lead_and_scheduled_msg(db, campaign.id, "transient@test.com", scheduled_at=now)

    sender = FakeSender()
    sender.fail_mode = "transient"
    sender.fail_error = "451 4.3.0 Mailbox busy"
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    # 1. First attempt fails -> rescheduled at now + 10m
    worker.run_once()
    db.refresh(msg)
    assert msg.status == EmailMessageStatus.SCHEDULED.value
    assert msg.retry_count == 1
    assert msg.scheduled_at == now + timedelta(minutes=10)

    # Fast forward 9 minutes: not due yet
    clock.advance(timedelta(minutes=9))
    worker.run_once()
    db.refresh(msg)
    assert msg.retry_count == 1

    # Fast forward 1 minute (total 10m): 2nd attempt fails -> rescheduled at now + 30m
    clock.advance(timedelta(minutes=1))
    t10 = clock.now()
    worker.run_once()
    db.refresh(msg)
    assert msg.status == EmailMessageStatus.SCHEDULED.value
    assert msg.retry_count == 2
    assert msg.scheduled_at == t10 + timedelta(minutes=30)

    # Fast forward 30 minutes: 3rd attempt fails -> status FAILED
    clock.advance(timedelta(minutes=30))
    worker.run_once()
    db.refresh(msg)
    assert msg.status == EmailMessageStatus.FAILED.value
    assert "Failed after 2 retries" in (msg.error or "")


def test_worker_permanent_error_bounces_lead(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify permanent 5xx error marks email failed and lead bounced with an Event."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    lead, msg = _seed_lead_and_scheduled_msg(
        db, campaign.id, "bounced@target.com", scheduled_at=clock.now()
    )

    sender = FakeSender()
    sender.fail_mode = "permanent"
    sender.fail_error = "550 5.1.1 Recipient does not exist"
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    worker.run_once()
    db.refresh(msg)
    db.refresh(lead)

    assert msg.status == EmailMessageStatus.FAILED.value
    assert "550" in (msg.error or "")
    assert lead.status == LeadStatus.BOUNCED.value

    # Check bounced Event
    bounced_event = db.scalar(
        select(Event).where(Event.lead_id == lead.id, Event.type == EventType.BOUNCED.value)
    )
    assert bounced_event is not None
    assert "550" in str(bounced_event.payload)


def test_worker_auth_error_triggers_killswitch(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify auth failure immediately engages kill-switch and halts worker loop."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    _, msg = _seed_lead_and_scheduled_msg(
        db, campaign.id, "authtest@acme.com", scheduled_at=clock.now()
    )

    sender = FakeSender()
    sender.fail_mode = "auth"
    sender.fail_error = "535 Authentication credentials invalid"
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    assert not is_killswitch_active(db)

    worker.run_once()
    db.refresh(msg)

    # Killswitch engaged!
    assert is_killswitch_active(db)
    reason_val = db.scalar(select(Setting.value).where(Setting.key == "KILLSWITCH_REASON"))
    assert "535" in (reason_val or "")

    # Message marked failed
    assert msg.status == EmailMessageStatus.FAILED.value

    # Worker running flag cleared
    assert not worker._running


def test_two_workers_racing_on_same_email(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify atomic claiming prevents race conditions when two workers target the same email."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    lead, msg = _seed_lead_and_scheduled_msg(
        db, campaign.id, "race@acme.com", scheduled_at=clock.now()
    )

    sender1 = FakeSender()
    sender2 = FakeSender()
    worker1 = Worker(sender=sender1, clock=clock, session_factory=factory)
    worker2 = Worker(sender=sender2, clock=clock, session_factory=factory)

    # Worker 1 runs and claims
    sent1 = worker1.process_message(msg.id, db, sender1, clock.now())
    # Worker 2 simultaneously runs on same msg.id
    sent2 = worker2.process_message(msg.id, db, sender2, clock.now())

    assert sent1 is True
    assert sent2 is True

    # Only ONE sender sent it
    assert len(sender1.sent_messages) == 1
    assert len(sender2.sent_messages) == 0

    # Only one 'sent' event exists
    events = db.scalars(
        select(Event).where(Event.lead_id == lead.id, Event.type == EventType.SENT.value)
    ).all()
    assert len(events) == 1


def test_crash_recovery_stuck_sending_row(worker_db: tuple[Session, sessionmaker]) -> None:
    """Verify startup recovery transitions stuck 'sending' emails (>10m) to 'needs_review'."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    now = clock.now()

    # Seed email stuck in sending for 15 minutes
    lead, msg = _seed_lead_and_scheduled_msg(
        db, campaign.id, "stuck@acme.com", scheduled_at=now - timedelta(minutes=20)
    )
    msg.status = EmailMessageStatus.SENDING.value
    msg.sending_started_at = now - timedelta(minutes=15)
    db.commit()

    recovered = recover_stuck_sending_messages(db, clock)
    assert recovered == 1

    db.refresh(msg)
    assert msg.status == EmailMessageStatus.NEEDS_REVIEW.value
    assert "manual review" in (msg.error or "")

    # Running worker will NOT send this email
    sender = FakeSender()
    worker = Worker(sender=sender, clock=clock, session_factory=factory)
    sent = worker.run_once()
    assert sent == 0
    assert len(sender.sent_messages) == 0


def test_suppressed_lead_between_planning_and_sending_is_skipped(
    worker_db: tuple[Session, sessionmaker],
) -> None:
    """Verify leads suppressed after planning are skipped during pre-send check."""
    db, factory = worker_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    lead, msg = _seed_lead_and_scheduled_msg(
        db, campaign.id, "suppress_late@acme.com", scheduled_at=clock.now()
    )

    # Add suppression after planning
    db.add(Suppression(email="suppress_late@acme.com", reason="opted out"))
    db.commit()

    sender = FakeSender()
    worker = Worker(sender=sender, clock=clock, session_factory=factory)

    sent = worker.run_once()
    assert sent == 0
    assert len(sender.sent_messages) == 0

    db.refresh(msg)
    db.refresh(lead)
    assert msg.status == EmailMessageStatus.SKIPPED.value
    assert lead.status == LeadStatus.UNSUBSCRIBED.value
