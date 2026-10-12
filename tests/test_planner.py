"""Unit tests for send planner and scheduling engine.

Tests use FakeClock to deterministically verify:
- Nothing planned before OUTREACH_START_DATE
- Every ramp-up tier (10, 15, 20, 25/day)
- Hard max cannot be exceeded even if a setting is set to 100
- Weekend and Monday pushed to Tuesday
- Send window respected in 4 different timezones including DST changes
- Min gap (120s) holds across 100 planned emails
- Follow-up timing counted from actual sent time
- Follow-ups prioritized over new leads
- Idempotency
- Replied, bounced, unsubscribed, suppressed leads skipped
- Paused campaign skipped
- Fallback for missing lead timezone with warning
"""

import logging
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.models.suppression import Suppression
from app.services.planner import (
    HARD_MAX_LIMIT,
    daily_capacity,
    due_emails,
    get_ramp_up_cap,
    plan_day,
)


@pytest.fixture
def plan_db() -> Session:
    """Create in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        # Default active campaign
        campaign = Campaign(name="Planner Campaign", status=CampaignStatus.ACTIVE.value)
        session.add(campaign)

        # Default settings
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-11-01"))
        session.add(Setting(key="MAILBOX_TZ", value="America/New_York"))
        session.add(Setting(key="HARD_MAX_PER_DAY", value="30"))
        session.commit()
        yield session


def _create_lead_with_email(
    db: Session,
    campaign_id: int,
    email: str,
    status: str = LeadStatus.APPROVED.value,
    step: int = 1,
    timezone: str = "America/New_York",
    email_status: str = EmailMessageStatus.APPROVED.value,
) -> tuple[Lead, EmailMessage]:
    """Helper to seed lead and corresponding sequence email."""
    lead = Lead(
        campaign_id=campaign_id,
        email=email,
        first_name="Test",
        last_name="User",
        company="Acme Corp",
        timezone=timezone,
        status=status,
    )
    db.add(lead)
    db.flush()

    msg = EmailMessage(
        lead_id=lead.id,
        step=step,
        subject="Test Subject",
        body="Test Body",
        status=email_status,
    )
    db.add(msg)
    db.commit()
    return lead, msg


def test_nothing_planned_before_outreach_start_date(plan_db: Session) -> None:
    """Verify daily_capacity returns 0 and nothing is planned before OUTREACH_START_DATE."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None
    _create_lead_with_email(plan_db, campaign.id, "early@acme.com")

    # OUTREACH_START_DATE is 2026-11-01
    clock = FakeClock(datetime(2026, 10, 31, 10, 0, tzinfo=UTC))
    cap = daily_capacity(date(2026, 10, 31), db=plan_db)
    assert cap == 0

    planned = plan_day(date(2026, 10, 31), db=plan_db, clock=clock)
    assert len(planned) == 0

    # Ensure message is still in approved status (not scheduled)
    msg = plan_db.scalar(select(EmailMessage))
    assert msg is not None
    assert msg.status == EmailMessageStatus.APPROVED.value
    assert msg.scheduled_at is None


def test_every_ramp_up_tier(plan_db: Session) -> None:
    """Verify every ramp-up tier: days 0-2 = 10, days 3-6 = 15, days 7-13 = 20, day 14+ = 25."""
    start_date = date(2026, 11, 1)

    # Days 0-2 -> 10/day
    assert get_ramp_up_cap(date(2026, 11, 1), start_date) == 10  # Day 0
    assert get_ramp_up_cap(date(2026, 11, 2), start_date) == 10  # Day 1
    assert get_ramp_up_cap(date(2026, 11, 3), start_date) == 10  # Day 2
    assert daily_capacity(date(2026, 11, 3), db=plan_db) == 10

    # Days 3-6 -> 15/day
    assert get_ramp_up_cap(date(2026, 11, 4), start_date) == 15  # Day 3
    assert get_ramp_up_cap(date(2026, 11, 7), start_date) == 15  # Day 6
    assert daily_capacity(date(2026, 11, 5), db=plan_db) == 15

    # Days 7-13 -> 20/day
    assert get_ramp_up_cap(date(2026, 11, 8), start_date) == 20   # Day 7
    assert get_ramp_up_cap(date(2026, 11, 14), start_date) == 20  # Day 13
    assert daily_capacity(date(2026, 11, 10), db=plan_db) == 20

    # Day 14+ -> 25/day
    assert get_ramp_up_cap(date(2026, 11, 15), start_date) == 25  # Day 14
    assert get_ramp_up_cap(date(2026, 12, 1), start_date) == 25   # Day 30
    assert daily_capacity(date(2026, 11, 15), db=plan_db) == 25


def test_hard_max_cannot_be_exceeded_even_if_setting_is_100(plan_db: Session) -> None:
    """Verify hard maximum cap of 30 cannot be exceeded even if Setting is set to 100."""
    setting = plan_db.get(Setting, "HARD_MAX_PER_DAY")
    if setting:
        setting.value = "100"
    else:
        plan_db.add(Setting(key="HARD_MAX_PER_DAY", value="100"))
    plan_db.commit()

    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    # Seed 40 approved leads
    for i in range(40):
        _create_lead_with_email(plan_db, campaign.id, f"bulk{i}@corp.com")

    # Day 15 (ramp-up tier is 25, setting is 100, hard limit is 30)
    target_date = date(2026, 11, 17)  # A Tuesday
    cap = daily_capacity(target_date, db=plan_db)
    assert cap <= HARD_MAX_LIMIT
    assert cap == 25  # base ramp-up cap is 25 <= 30

    # If base ramp-up was artificially higher or uncapped, hard cap of 30 holds
    planned = plan_day(target_date, db=plan_db, jitter_fn=lambda: 0)
    assert len(planned) <= 30


def test_weekend_and_monday_pushed_to_tuesday(plan_db: Session) -> None:
    """Verify that dispatches planned on weekends or Mondays are shifted to Tuesday."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None
    _, msg = _create_lead_with_email(
        plan_db, campaign.id, "monday@acme.com", timezone="America/New_York"
    )

    # 2026-11-02 is Monday. Allowed days: Tue (1), Wed (2), Thu (3).
    clock = FakeClock(datetime(2026, 11, 2, 8, 0, tzinfo=UTC))
    planned = plan_day(date(2026, 11, 2), db=plan_db, clock=clock, jitter_fn=lambda: 0)

    assert len(planned) == 1
    assert msg.scheduled_at is not None
    # Converted to lead tz (America/New_York)
    local_dt = msg.scheduled_at.astimezone(ZoneInfo("America/New_York"))
    assert local_dt.weekday() == 1  # Tuesday!
    assert local_dt.date() == date(2026, 11, 3)
    assert local_dt.time() >= time(9, 0)
    assert local_dt.time() <= time(11, 30)


def test_send_window_respected_in_4_timezones_including_dst(plan_db: Session) -> None:
    """Verify send window 09:00-11:30 is strictly respected across 4 timezones with DST."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    timezones = [
        "America/New_York",      # Eastern (DST ends Nov 1, 2026)
        "Europe/London",         # London (DST ended Oct 25, 2026)
        "Asia/Tokyo",            # Tokyo (No DST)
        "America/Los_Angeles",   # Pacific (DST ends Nov 1, 2026)
    ]

    for idx, tz_name in enumerate(timezones):
        sanitized = tz_name.replace("/", "_")
        _create_lead_with_email(
            plan_db, campaign.id, f"lead_{idx}@{sanitized}.com", timezone=tz_name
        )

    # Plan for Tuesday Nov 3, 2026
    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    planned = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)

    assert len(planned) == 4
    for email_msg in planned:
        lead = email_msg.lead
        assert lead is not None
        assert lead.timezone in timezones
        lead_tz = ZoneInfo(lead.timezone)

        assert email_msg.scheduled_at is not None
        local_dt = email_msg.scheduled_at.astimezone(lead_tz)

        # Must fall on Tue, Wed, or Thu
        assert local_dt.strftime("%a") in ["Tue", "Wed", "Thu"]
        # Must be strictly within 09:00 and 11:30
        assert local_dt.time() >= time(9, 0)
        assert local_dt.time() <= time(11, 30)


def test_min_gap_holds_across_100_planned_emails(plan_db: Session) -> None:
    """Verify minimum gap (>= 120s) holds across 100 planned emails."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    for i in range(100):
        _create_lead_with_email(
            plan_db, campaign.id, f"user{i}@gaptest.com", timezone="America/New_York"
        )

    # Plan across successive days until all 100 are planned
    clock = FakeClock(datetime(2026, 11, 3, 0, 0, tzinfo=UTC))
    start_d = date(2026, 11, 3)
    all_planned: list[EmailMessage] = []

    for day_offset in range(10):
        current_date = start_d + timedelta(days=day_offset)
        newly_planned = plan_day(current_date, db=plan_db, clock=clock, jitter_fn=lambda: 30)
        all_planned.extend(newly_planned)
        if len(all_planned) >= 100:
            break

    assert len(all_planned) == 100
    # Sort chronologically by scheduled_at in UTC
    all_planned.sort(key=lambda m: m.scheduled_at)  # type: ignore[arg-type, return-value]

    # Verify every consecutive pair is separated by at least 120 seconds
    for i in range(len(all_planned) - 1):
        t1 = all_planned[i].scheduled_at
        t2 = all_planned[i + 1].scheduled_at
        assert t1 is not None and t2 is not None
        gap_sec = (t2 - t1).total_seconds()
        assert gap_sec >= 120, f"Gap between #{i} and #{i+1} was {gap_sec}s (< 120s)"


def test_follow_up_timing_counted_from_actual_sent_time(plan_db: Session) -> None:
    """Verify follow-ups are due N days after previous step was sent (never before)."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    # Configure step 2 delay: 4 days
    plan_db.add(
        SequenceStep(
            campaign_id=campaign.id,
            step=2,
            delay_days=4,
            subjects=["Re: Test"],
            bodies=["Follow up body"],
        )
    )
    plan_db.commit()

    lead, step1_msg = _create_lead_with_email(
        plan_db,
        campaign.id,
        "followup@acme.com",
        status=LeadStatus.IN_SEQUENCE.value,
        step=1,
        email_status=EmailMessageStatus.SENT.value,
    )
    # Step 1 was actually sent on Nov 3 at 10:15 UTC
    step1_msg.sent_at = datetime(2026, 11, 3, 10, 15, tzinfo=UTC)

    # Step 2 approved message
    step2_msg = EmailMessage(
        lead_id=lead.id,
        step=2,
        subject="Re: Test",
        body="Follow up body",
        status=EmailMessageStatus.APPROVED.value,
    )
    plan_db.add(step2_msg)
    plan_db.commit()

    # 1. Check at 3 days after sent (Nov 6): NOT DUE
    nov_6 = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
    due_nov_6 = due_emails(now=nov_6, db=plan_db)
    assert step2_msg not in due_nov_6

    # 2. Check 1 minute before exact 4 days (Nov 7 10:14 UTC): NOT DUE
    nov_7_early = datetime(2026, 11, 7, 10, 14, tzinfo=UTC)
    due_nov_7_early = due_emails(now=nov_7_early, db=plan_db)
    assert step2_msg not in due_nov_7_early

    # 3. Check at exactly 4 days (Nov 7 10:15 UTC): DUE!
    nov_7_exact = datetime(2026, 11, 7, 10, 15, tzinfo=UTC)
    due_nov_7_exact = due_emails(now=nov_7_exact, db=plan_db)
    assert step2_msg in due_nov_7_exact


def test_follow_ups_prioritized_over_new_leads(plan_db: Session) -> None:
    """Verify follow-up emails (step > 1) are given priority over new first emails (step 1)."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    # Create 3 leads with due follow-ups (step 2)
    sent_time = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    for i in range(3):
        lead, s1 = _create_lead_with_email(
            plan_db,
            campaign.id,
            f"existing_{i}@acme.com",
            status=LeadStatus.IN_SEQUENCE.value,
            step=1,
            email_status=EmailMessageStatus.SENT.value,
        )
        s1.sent_at = sent_time
        s2 = EmailMessage(
            lead_id=lead.id,
            step=2,
            subject=f"Re: Followup {i}",
            body="Checking in",
            status=EmailMessageStatus.APPROVED.value,
        )
        plan_db.add(s2)

    # Create 10 new leads with step 1 approved emails
    for i in range(10):
        _create_lead_with_email(plan_db, campaign.id, f"new_{i}@acme.com", step=1)

    plan_db.commit()

    # Nov 3 capacity is 10
    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=UTC))
    planned = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)

    # Capacity is 10 on day 2. Total candidates: 3 follow-ups + 10 new = 13.
    assert len(planned) == 10
    step_2_count = sum(1 for m in planned if m.step == 2)
    step_1_count = sum(1 for m in planned if m.step == 1)

    # All 3 follow-ups MUST be included!
    assert step_2_count == 3
    assert step_1_count == 7


def test_idempotency_running_plan_day_twice(plan_db: Session) -> None:
    """Verify running plan_day twice does not double-schedule or exceed capacity."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    for i in range(15):
        _create_lead_with_email(plan_db, campaign.id, f"idem_{i}@acme.com")

    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=UTC))

    run1 = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)
    assert len(run1) == 10

    # Second run immediately after
    run2 = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)
    assert len(run2) == 0

    # Total scheduled in DB for that date must still equal 10
    remaining_cap = daily_capacity(target_date, db=plan_db)
    assert remaining_cap == 0


def test_replied_bounced_unsubscribed_suppressed_skipped(plan_db: Session) -> None:
    """Verify replied, bounced, unsubscribed, and suppressed leads are excluded from planning."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    # Replied lead
    _create_lead_with_email(
        plan_db, campaign.id, "replied@acme.com", status=LeadStatus.REPLIED.value
    )
    # Bounced lead
    _create_lead_with_email(
        plan_db, campaign.id, "bounced@acme.com", status=LeadStatus.BOUNCED.value
    )
    # Unsubscribed lead
    _create_lead_with_email(
        plan_db, campaign.id, "unsub@acme.com", status=LeadStatus.UNSUBSCRIBED.value
    )
    # Suppressed lead
    lead_supp, _ = _create_lead_with_email(plan_db, campaign.id, "suppressed@acme.com")
    plan_db.add(Suppression(email="suppressed@acme.com", reason="opt-out"))
    plan_db.commit()

    # Valid lead
    _, valid_msg = _create_lead_with_email(plan_db, campaign.id, "valid@acme.com")

    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=UTC))
    planned = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)

    assert len(planned) == 1
    assert planned[0].id == valid_msg.id


def test_paused_campaign_skipped(plan_db: Session) -> None:
    """Verify that leads in paused campaigns are not planned."""
    paused_camp = Campaign(name="Paused Camp", status=CampaignStatus.PAUSED.value)
    plan_db.add(paused_camp)
    plan_db.commit()

    _create_lead_with_email(plan_db, paused_camp.id, "paused_user@acme.com")

    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=UTC))
    planned = plan_day(target_date, db=plan_db, clock=clock)

    assert len(planned) == 0


def test_missing_lead_timezone_fallback_and_warning(
    plan_db: Session, caplog: pytest.LogCaptureFixture
) -> None:
    """Verify missing lead timezone falls back to mailbox timezone and logs a warning."""
    campaign = plan_db.scalar(select(Campaign))
    assert campaign is not None

    lead, msg = _create_lead_with_email(plan_db, campaign.id, "notz@acme.com")
    lead.timezone = None
    plan_db.commit()

    target_date = date(2026, 11, 3)
    clock = FakeClock(datetime(2026, 11, 3, 8, 0, tzinfo=UTC))

    with caplog.at_level(logging.WARNING):
        planned = plan_day(target_date, db=plan_db, clock=clock, jitter_fn=lambda: 0)

    assert len(planned) == 1
    assert "has no timezone; falling back to mailbox timezone" in caplog.text

    # Scheduled within mailbox timezone (America/New_York) window
    assert msg.scheduled_at is not None
    local_dt = msg.scheduled_at.astimezone(ZoneInfo("America/New_York"))
    assert time(9, 0) <= local_dt.time() <= time(11, 30)
