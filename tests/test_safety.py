"""Comprehensive test suite for the Safety Monitor, Emergency Kill-Switch, and Alerting.

Tests:
1. Minimum sample size suppression (< 20 sends suppresses rate-based rules).
2. Each WARNING rule:
   - bounce_rate >= 1.5%
   - unsubscribe_rate >= 3%
   - failure_rate >= 5%
3. Each CRITICAL rule trips kill-switch:
   - bounce_rate > 2% (with sample size >= 20)
   - 2+ complaint_signals in last 7 days (absolute)
   - failure_rate >= 15% (with attempts >= 20)
   - 1+ auth_or_block_errors (absolute)
   - 3 hard bounces within last 10 sends (absolute)
4. Worker stops sending when kill-switch is active, but inbox polling continues.
5. Resume requires explicit typed acknowledgement + note, and halves cap for 3 days.
6. Alert rate-limiting (suppresses duplicates within 1 hour).
7. Daily summary dispatched once per calendar day at configurable hour.
8. Full scenario: 40 normal sends, then 3 bounces arrive -> stops and alerts before 41st email.
"""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.models.setting import Setting
from app.services.inbox import IncomingMail, MailboxReader
from app.services.notifier import (
    FakeNotifier,
    get_latest_alerts,
    is_alert_rate_limited,
    record_alert_dispatched,
    send_daily_summary,
)
from app.services.planner import daily_capacity, due_emails, plan_day
from app.services.safety import (
    calculate_health_metrics,
    evaluate_and_enforce_safety,
    evaluate_safety_rules,
    is_cap_halved_from_resume,
    is_killswitch_active,
    resume_killswitch,
    trigger_killswitch,
)
from app.services.sender import FakeSender
from app.worker import Worker, get_daily_send_cap


@pytest.fixture
def safety_db() -> tuple[Session, sessionmaker]:
    """Create in-memory SQLite database session and factory."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sm() as session:
        campaign = Campaign(name="Safety Campaign", status=CampaignStatus.ACTIVE.value)
        session.add(campaign)

        session.add(Setting(key="OUTREACH_START_DATE", value="2026-10-01"))
        session.add(Setting(key="MAILBOX_TZ", value="UTC"))
        session.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
        session.add(Setting(key="MIN_GAP_SECONDS", value="0"))
        session.add(Setting(key="MAX_JITTER_SECONDS", value="0"))
        session.add(Setting(key="DAILY_SUMMARY_HOUR", value="9"))
        session.commit()
        yield session, sm


def _create_send_history(
    session: Session,
    campaign_id: int,
    total_sent: int,
    bounces: int = 0,
    unsubscribes: int = 0,
    base_time: datetime | None = None,
) -> list[Lead]:
    """Helper to populate history of sent emails, bounces, and unsubscribes."""
    ref_time = base_time or datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
    leads: list[Lead] = []

    for i in range(total_sent):
        lead_status = LeadStatus.IN_SEQUENCE.value
        if i < bounces:
            lead_status = LeadStatus.BOUNCED.value
        elif i < bounces + unsubscribes:
            lead_status = LeadStatus.UNSUBSCRIBED.value

        lead = Lead(
            campaign_id=campaign_id,
            email=f"lead_{i}@example.com",
            first_name=f"Lead{i}",
            status=lead_status,
        )
        session.add(lead)
        session.flush()
        leads.append(lead)

        sent_dt = ref_time - timedelta(minutes=(total_sent - i) * 5)
        msg = EmailMessage(
            lead_id=lead.id,
            step=1,
            subject=f"Intro {i}",
            body="Hello lead, let us connect.",
            status=EmailMessageStatus.SENT.value,
            sent_at=sent_dt,
            message_id=f"<msg_{i}@sender.com>",
        )
        session.add(msg)
        session.flush()

        session.add(
            Event(
                lead_id=lead.id,
                type=EventType.SENT.value,
                payload={"step": 1, "message_id": msg.message_id},
                created_at=sent_dt,
            )
        )

        if lead_status == LeadStatus.BOUNCED.value:
            session.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.BOUNCED.value,
                    payload={"reason": "550 User not found"},
                    created_at=sent_dt + timedelta(minutes=1),
                )
            )
        elif lead_status == LeadStatus.UNSUBSCRIBED.value:
            session.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.UNSUBSCRIBED.value,
                    payload={"snippet": "unsubscribe please"},
                    created_at=sent_dt + timedelta(minutes=1),
                )
            )

    session.commit()
    return leads


# =============================================================================
# 1. Minimum Sample Size Suppression
# =============================================================================
def test_minimum_sample_size_suppression(safety_db: tuple[Session, sessionmaker]) -> None:
    """Rate-based rules must NOT fire when sample size < 20 sends."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))

    # 10 sends with 1 bounce -> bounce rate = 10% (> 2.0% threshold)
    _create_send_history(session, campaign.id, total_sent=10, bounces=1)

    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    metrics = calculate_health_metrics(session, now)

    assert metrics.total_sent_100 == 10
    assert metrics.sample_size_ok is False
    assert metrics.bounce_rate == 10.0

    level, reasons = evaluate_safety_rules(metrics)
    # Rate-based critical/warning must NOT fire because sample_size < 20
    assert level == "OK"
    assert len(reasons) == 0


# =============================================================================
# 2. WARNING Rules Evaluation
# =============================================================================
def test_warning_rules(safety_db: tuple[Session, sessionmaker]) -> None:
    """Test each WARNING threshold when sample size >= 20."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)

    # 2a. Bounce rate >= 1.5% and <= 2.0% (e.g. 1 out of 60 = 1.67%)
    _create_send_history(session, campaign.id, total_sent=60, bounces=1, base_time=now)
    metrics = calculate_health_metrics(session, now)
    level, reasons = evaluate_safety_rules(metrics)
    assert level == "WARNING"
    assert any("Bounce rate" in r and "warning" in r for r in reasons)

    # 2b. Unsubscribe rate >= 3.0% (e.g. 2 unsubs out of 60 = 3.33%)
    # Let's reset and test unsubs
    session.query(Event).delete()
    session.query(EmailMessage).delete()
    session.query(Lead).delete()
    session.commit()

    _create_send_history(session, campaign.id, total_sent=50, unsubscribes=2, base_time=now)
    metrics = calculate_health_metrics(session, now)
    level, reasons = evaluate_safety_rules(metrics)
    assert level == "WARNING"
    assert any("Unsubscribe rate" in r for r in reasons)

    # 2c. Failure rate >= 5.0% and < 15.0% (e.g. 2 failures out of 30 attempts = 6.67%)
    session.query(Event).delete()
    session.query(EmailMessage).delete()
    session.query(Lead).delete()
    session.commit()

    _create_send_history(session, campaign.id, total_sent=28, base_time=now)
    # Add 2 failed messages
    for i in range(2):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"failed_{i}@example.com",
            status=LeadStatus.IN_SEQUENCE.value,
        )
        session.add(lead)
        session.flush()
        session.add(
            EmailMessage(
                lead_id=lead.id,
                step=1,
                subject=f"Failed {i}",
                body="Body",
                status=EmailMessageStatus.FAILED.value,
                error="503 Transient delivery failed",
            )
        )
    session.commit()

    metrics = calculate_health_metrics(session, now)
    assert metrics.total_attempts_50 == 30
    assert metrics.failed_attempts_50 == 2
    level, reasons = evaluate_safety_rules(metrics)
    assert level == "WARNING"
    assert any("failure rate" in r.lower() for r in reasons)


# =============================================================================
# 3. CRITICAL Rules Trip Kill-Switch
# =============================================================================
def test_critical_rule_bounce_rate_over_2_percent(safety_db: tuple[Session, sessionmaker]) -> None:
    """CRITICAL: bounce_rate > 2% (with sample size >= 20) trips killswitch."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    notifier = FakeNotifier()
    clock = FakeClock(now)

    # 40 sends with 2 bounces -> 5.0% bounce rate (> 2.0%)
    _create_send_history(session, campaign.id, total_sent=40, bounces=2, base_time=now)

    level, reasons, _ = evaluate_and_enforce_safety(session, notifier=notifier, clock=clock)
    assert level == "CRITICAL"
    assert is_killswitch_active(session) is True
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].level == "CRITICAL"

    # Campaign must be paused
    session.refresh(campaign)
    assert campaign.status == CampaignStatus.PAUSED.value


def test_critical_rule_complaint_signals(safety_db: tuple[Session, sessionmaker]) -> None:
    """CRITICAL: 2 or more complaint_signals in last 7 days (absolute rule)."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    notifier = FakeNotifier()
    clock = FakeClock(now)

    # Only 5 sends total (sample size < 20), but 2 abuse complaint replies arrive
    leads = _create_send_history(session, campaign.id, total_sent=5, base_time=now)

    session.add(
        Reply(
            lead_id=leads[0].id,
            from_email=leads[0].email,
            to_email="sender@example.com",
            subject="Re: Intro",
            body="I will report your email as spam to your provider",
            snippet="report your email as spam",
            classification="reply_other",
            created_at=now,
        )
    )
    session.add(
        Reply(
            lead_id=leads[1].id,
            from_email=leads[1].email,
            to_email="sender@example.com",
            subject="Re: Intro",
            body="Stop this harassment immediately or expect legal action",
            snippet="harassment legal action",
            classification="reply_other",
            created_at=now,
        )
    )
    session.commit()

    level, reasons, _ = evaluate_and_enforce_safety(session, notifier=notifier, clock=clock)
    assert level == "CRITICAL"
    assert is_killswitch_active(session) is True
    assert any("complaint signals" in r for r in reasons)


def test_critical_rule_failure_rate_over_15_percent(
    safety_db: tuple[Session, sessionmaker],
) -> None:
    """CRITICAL: failure_rate >= 15% (over 20+ attempts)."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    notifier = FakeNotifier()
    clock = FakeClock(now)

    # 17 sent + 4 failed = 21 attempts, 4/21 = 19.0% failure rate
    _create_send_history(session, campaign.id, total_sent=17, base_time=now)
    for i in range(4):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"fail_{i}@test.com",
            status=LeadStatus.IN_SEQUENCE.value,
        )
        session.add(lead)
        session.flush()
        session.add(
            EmailMessage(
                lead_id=lead.id,
                step=1,
                subject=f"Fail {i}",
                body="Body",
                status=EmailMessageStatus.FAILED.value,
                error="Permanent failure",
            )
        )
    session.commit()

    level, reasons, _ = evaluate_and_enforce_safety(session, notifier=notifier, clock=clock)
    assert level == "CRITICAL"
    assert is_killswitch_active(session) is True


def test_critical_rule_auth_or_block_errors(safety_db: tuple[Session, sessionmaker]) -> None:
    """CRITICAL: 1 or more auth_or_block_errors (absolute rule)."""
    session, _ = safety_db
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    notifier = FakeNotifier()
    clock = FakeClock(now)

    session.add(
        Event(
            lead_id=None,
            type=EventType.ERROR.value,
            payload={"error": "SMTPAuthenticationError: 535 5.7.8 Invalid credentials"},
            created_at=now,
        )
    )
    session.commit()

    level, reasons, _ = evaluate_and_enforce_safety(session, notifier=notifier, clock=clock)
    assert level == "CRITICAL"
    assert is_killswitch_active(session) is True
    assert any("authentication or provider block" in r for r in reasons)


def test_critical_rule_3_bounces_in_last_10_sends(safety_db: tuple[Session, sessionmaker]) -> None:
    """CRITICAL: 3 hard bounces within last 10 sends (absolute rule)."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    notifier = FakeNotifier()
    clock = FakeClock(now)

    # 8 total sends, 3 of which bounced (within last 10)
    _create_send_history(session, campaign.id, total_sent=8, bounces=3, base_time=now)

    level, reasons, _ = evaluate_and_enforce_safety(session, notifier=notifier, clock=clock)
    assert level == "CRITICAL"
    assert is_killswitch_active(session) is True
    assert any("Cluster of 3 hard bounces" in r for r in reasons)


# =============================================================================
# 4. Worker Stops Sending but Inbox Polling Continues
# =============================================================================
class MockInboxReader(MailboxReader):
    """Mock reader to verify inbox polling executes even during kill-switch."""

    def __init__(self, incoming: list[IncomingMail]) -> None:
        self.incoming = incoming
        self.poll_called = False

    def fetch_new(self, since_uid: int = 0) -> list[IncomingMail]:
        self.poll_called = True
        return [m for m in self.incoming if m.uid > since_uid]


def test_worker_stops_sending_when_killswitch_on_but_inbox_polls(
    safety_db: tuple[Session, sessionmaker],
) -> None:
    """When kill-switch is on, worker sends nothing, but inbox poll continues."""
    session, factory = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)
    notifier = FakeNotifier()

    # Trip killswitch
    trigger_killswitch(session, reason="Manual emergency pause", clock=clock, notifier=notifier)
    assert is_killswitch_active(session) is True

    # Prepare candidate email that would otherwise be due
    lead = Lead(
        campaign_id=campaign.id,
        email="target@example.com",
        status=LeadStatus.APPROVED.value,
        timezone="UTC",
    )
    session.add(lead)
    session.flush()

    msg = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Hello",
        body="Body",
        status=EmailMessageStatus.SCHEDULED.value,
        scheduled_at=now - timedelta(minutes=5),
    )
    session.add(msg)
    session.commit()

    # Prepare an incoming reply to be polled
    incoming_mail = IncomingMail(
        uid=101,
        raw_bytes=b"Subject: Re: Hello\n\nThanks, let's talk next week!",
        message_id="<reply_1@example.com>",
        from_email="target@example.com",
        from_name="Target",
        to_email="sender@company.com",
        subject="Re: Hello",
        body_text="Thanks, let's talk next week!",
        date=now,
        in_reply_to="<msg_1@example.com>",
    )
    inbox_reader = MockInboxReader([incoming_mail])
    fake_sender = FakeSender()

    worker = Worker(
        clock=clock,
        sender=fake_sender,
        session_factory=factory,
        inbox_reader=inbox_reader,
        notifier=notifier,
    )

    # Run worker cycle
    sent_count = worker.run_once(poll_inbox_now=True)

    # Outbound sending must be halted
    assert sent_count == 0
    assert len(fake_sender.sent_messages) == 0

    # But inbox reader MUST have been called and reply processed
    assert inbox_reader.poll_called is True
    session.refresh(lead)
    assert lead.status == LeadStatus.REPLIED.value

    # Planner must also return 0 candidates
    assert len(due_emails(now=now, db=session)) == 0
    assert len(plan_day("2026-10-15", db=session, clock=clock)) == 0


# =============================================================================
# 5. Resume Lifecycle and 3-Day Cap Halving
# =============================================================================
def test_resume_killswitch_lifecycle(safety_db: tuple[Session, sessionmaker]) -> None:
    """Resumption requires exact typed acknowledgement + note, and halves cap for 3 days."""
    session, _ = safety_db
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(now)
    notifier = FakeNotifier()

    trigger_killswitch(session, reason="Critical alert", clock=clock, notifier=notifier)
    assert is_killswitch_active(session) is True

    # 5a. Invalid acknowledgement rejected
    with pytest.raises(ValueError, match="Invalid acknowledgement"):
        resume_killswitch("yes please", "Fixed DNS", session, clock=clock, notifier=notifier)

    # 5b. Empty or too short note rejected
    with pytest.raises(ValueError, match="descriptive human note"):
        resume_killswitch("I ACKNOWLEDGE", "ok", session, clock=clock, notifier=notifier)

    # 5c. Valid resume
    res = resume_killswitch(
        acknowledgement="I ACKNOWLEDGE",
        note="Identified SMTP misconfiguration and rotated credentials",
        db=session,
        clock=clock,
        notifier=notifier,
    )
    assert res is True
    assert is_killswitch_active(session) is False

    # Event logged
    ev = session.scalar(
        select(Event)
        .where(Event.type == EventType.KILLSWITCH.value)
        .order_by(Event.id.desc())
        .limit(1)
    )
    assert ev is not None
    assert ev.payload["state"] == "resumed"
    assert ev.payload["cap_halved_days"] == 3

    # 5d. Test cap halving for 3 calendar days (days 0, 1, 2 = halved; day 3 = restored)
    # Outreach start date was 2026-10-01. On day 14+, ramp-up cap = 25.
    d0 = date(2026, 10, 15)
    d1 = date(2026, 10, 16)
    d2 = date(2026, 10, 17)
    d3 = date(2026, 10, 18)

    assert is_cap_halved_from_resume(session, d0) is True
    assert is_cap_halved_from_resume(session, d1) is True
    assert is_cap_halved_from_resume(session, d2) is True
    assert is_cap_halved_from_resume(session, d3) is False

    # Baseline cap without halving on day 14+ is 25. Halved = 25 // 2 = 12
    assert daily_capacity(d0, session) == 12
    assert daily_capacity(d1, session) == 12
    assert daily_capacity(d2, session) == 12
    assert daily_capacity(d3, session) == 25

    # Check worker daily cap function as well
    assert get_daily_send_cap(session, d0) == 12
    assert get_daily_send_cap(session, d3) == 25


# =============================================================================
# 6. Alert Rate-Limiting
# =============================================================================
def test_alert_rate_limiting(safety_db: tuple[Session, sessionmaker]) -> None:
    """Duplicate alerts with same title are rate-limited to once per hour."""
    session, _ = safety_db
    t0 = datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC)
    clock = FakeClock(t0)

    title = "High Bounce Rate"
    assert is_alert_rate_limited(session, title, clock) is False

    # Record first dispatch
    record_alert_dispatched(session, "WARNING", title, "Bounce rate reached 1.8%", clock=clock)

    # Check immediate repeat -> rate limited!
    assert is_alert_rate_limited(session, title, clock) is True

    # Advance 45 minutes -> still rate limited!
    clock.advance(timedelta(minutes=45))
    assert is_alert_rate_limited(session, title, clock) is True

    # Advance past 60 minutes -> allowed again!
    clock.advance(timedelta(minutes=16))
    assert is_alert_rate_limited(session, title, clock) is False


# =============================================================================
# 7. Daily Summary
# =============================================================================
def test_daily_summary(safety_db: tuple[Session, sessionmaker]) -> None:
    """Send once-a-day summary at configurable hour (default 09:00)."""
    session, _ = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    notifier = FakeNotifier()

    # Populate some sends, bounces, and replies in the last 24h
    now = datetime(2026, 10, 15, 8, 30, 0, tzinfo=UTC)
    clock = FakeClock(now)

    _create_send_history(session, campaign.id, total_sent=15, bounces=1, base_time=now)

    # 1. At 08:30 -> Not summary hour (summary hour = 9) -> no summary sent
    sent = send_daily_summary(session, notifier=notifier, clock=clock)
    assert sent is None
    assert len(notifier.alerts) == 0

    # 2. Advance to 09:05 -> Summary hour!
    clock.advance(timedelta(minutes=35))
    sent = send_daily_summary(session, notifier=notifier, clock=clock)
    assert sent is not None
    assert len(notifier.alerts) == 1
    summary_alert = notifier.alerts[0]
    assert "Daily Summary" in summary_alert.title
    assert "Emails Dispatched (24h): 15" in summary_alert.message
    assert "Hard Bounces (24h): 1" in summary_alert.message

    # 3. Running again at 09:40 on same day -> must not repeat
    clock.advance(timedelta(minutes=35))
    sent_again = send_daily_summary(session, notifier=notifier, clock=clock)
    assert sent_again is None
    assert len(notifier.alerts) == 1

    # 4. Moving to next day at 09:10 -> dispatches new summary
    clock.advance(timedelta(days=1))
    sent_next_day = send_daily_summary(session, notifier=notifier, clock=clock)
    assert sent_next_day is not None
    assert len(notifier.alerts) == 2


# =============================================================================
# 8. Full Scenario: 40 Normal Sends, Then 3 Bounces Arrive -> Stops Before 41st
# =============================================================================
def test_full_scenario_40_sends_then_3_bounces(
    safety_db: tuple[Session, sessionmaker],
    capsys: pytest.CaptureFixture,
) -> None:
    """Full scenario: 40 normal sends, then 3 bounces arrive.

    System must halt itself and alert before the 41st email can be sent.
    Prints execution event log for audit verification.
    """
    session, factory = safety_db
    campaign = session.scalar(select(Campaign).limit(1))
    t0 = datetime(2026, 10, 15, 9, 0, 0, tzinfo=UTC)
    clock = FakeClock(t0)
    notifier = FakeNotifier()
    fake_sender = FakeSender()

    # Step 1: Dispatch 40 normal successful sends
    for i in range(40):
        lead = Lead(
            campaign_id=campaign.id,
            email=f"customer_{i}@domain.com",
            first_name=f"Customer{i}",
            status=LeadStatus.APPROVED.value,
            timezone="UTC",
        )
        session.add(lead)
        session.flush()

        # Send time
        clock.advance(timedelta(seconds=130))
        send_time = clock.now()

        msg = EmailMessage(
            lead_id=lead.id,
            step=1,
            subject=f"Introduction {i}",
            body="Hello, wanted to connect.",
            status=EmailMessageStatus.SENT.value,
            sent_at=send_time,
            message_id=f"<msg_{i}@sender.com>",
        )
        session.add(msg)
        session.add(
            Event(
                lead_id=lead.id,
                type=EventType.SENT.value,
                payload={"step": 1, "message_id": msg.message_id},
                created_at=send_time,
            )
        )
    session.commit()

    # Verify baseline health: 40 sends, 0 bounces -> OK
    m_baseline = calculate_health_metrics(session, clock.now())
    assert m_baseline.total_sent_100 == 40
    assert m_baseline.hard_bounces_last_10 == 0
    assert is_killswitch_active(session) is False

    # Step 2: Queue 41st email (scheduled and due right now)
    lead_41 = Lead(
        campaign_id=campaign.id,
        email="customer_41@domain.com",
        first_name="Customer41",
        status=LeadStatus.APPROVED.value,
        timezone="UTC",
    )
    session.add(lead_41)
    session.flush()

    msg_41 = EmailMessage(
        lead_id=lead_41.id,
        step=1,
        subject="Introduction 41",
        body="Hello Customer 41",
        status=EmailMessageStatus.SCHEDULED.value,
        scheduled_at=clock.now() - timedelta(minutes=1),
    )
    session.add(msg_41)
    session.commit()

    # Step 3: Inbound monitoring cycle - 3 hard bounces arrive from previous recipients
    bounce_body_1 = (
        "Action: failed\nStatus: 5.1.1\nDiagnostic-Code: 550 User not found customer_37@domain.com"
    )
    bounce_body_2 = (
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: 550 Mailbox does not exist customer_38@domain.com"
    )
    bounce_body_3 = (
        "Action: failed\nStatus: 5.1.1\n"
        "Diagnostic-Code: 550 Recipient rejected customer_39@domain.com"
    )

    bounced_emails = [
        IncomingMail(
            uid=201,
            raw_bytes=bounce_body_1.encode("utf-8"),
            message_id="<bounce_1@postmaster>",
            from_email="mailer-daemon@google.com",
            from_name="Mailer Daemon",
            to_email="sender@company.com",
            subject="Delivery Status Notification (Failure)",
            date=clock.now(),
            in_reply_to=None,
            body_text=bounce_body_1,
        ),
        IncomingMail(
            uid=202,
            raw_bytes=bounce_body_2.encode("utf-8"),
            message_id="<bounce_2@postmaster>",
            from_email="mailer-daemon@google.com",
            from_name="Mailer Daemon",
            to_email="sender@company.com",
            subject="Delivery Status Notification (Failure)",
            date=clock.now(),
            in_reply_to=None,
            body_text=bounce_body_2,
        ),
        IncomingMail(
            uid=203,
            raw_bytes=bounce_body_3.encode("utf-8"),
            message_id="<bounce_3@postmaster>",
            from_email="mailer-daemon@google.com",
            from_name="Mailer Daemon",
            to_email="sender@company.com",
            subject="Delivery Status Notification (Failure)",
            date=clock.now(),
            in_reply_to=None,
            body_text=bounce_body_3,
        ),
    ]

    mock_inbox = MockInboxReader(bounced_emails)
    worker = Worker(
        clock=clock,
        sender=fake_sender,
        session_factory=factory,
        inbox_reader=mock_inbox,
        notifier=notifier,
    )

    # Execute worker run_once() with inbox polling
    dispatched = worker.run_once(poll_inbox_now=True)

    # ASSERTIONS:
    # 1. Zero emails dispatched during this cycle
    assert dispatched == 0
    assert len(fake_sender.sent_messages) == 0

    # 2. Email 41 was NOT sent; status remains scheduled
    session.refresh(msg_41)
    assert msg_41.status == EmailMessageStatus.SCHEDULED.value
    assert msg_41.sent_at is None

    # 3. Kill-switch is actively engaged
    assert is_killswitch_active(session) is True

    # 4. Critical notification emitted
    critical_alerts = [a for a in notifier.alerts if a.level == "CRITICAL"]
    assert len(critical_alerts) >= 1
    assert "EMERGENCY KILL-SWITCH ENGAGED" in critical_alerts[0].title

    # 5. Dashboard banner alerts accessible
    banner_alerts = get_latest_alerts(session, limit=5)
    assert len(banner_alerts) >= 1
    alert_types = [a["type"] for a in banner_alerts]
    assert "killswitch" in alert_types or "alert" in alert_types
    assert any(a["level"] == "CRITICAL" for a in banner_alerts)

    # PRINT AUDIT LOG FOR VERIFICATION REPORT
    events = session.scalars(select(Event).order_by(Event.id.asc())).all()
    print("\n" + "=" * 80)
    print("SCENARIO AUDIT LOG: 40 SENDS -> 3 BOUNCES -> KILL-SWITCH ENGAGED")
    print("=" * 80)
    for ev in events[-10:]:
        iso_ts = ev.created_at.isoformat() if ev.created_at else ""
        print(f"[{iso_ts}] EVENT #{ev.id} TYPE: {ev.type:<12} | PAYLOAD: {ev.payload}")

    print("\nNOTIFIER ALERTS DISPATCHED:")
    for a in notifier.alerts:
        print(f"[{a.level}] {a.title}: {a.message}")
    print("=" * 80 + "\n")
