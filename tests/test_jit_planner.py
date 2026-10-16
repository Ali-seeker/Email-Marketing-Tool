"""Unit and scenario tests for the Just-In-Time (JIT) Lead Batch Processing Engine.

Validates that:
1. Multiple Mailboxes (e.g. 10 mailboxes x 30 = 300/day) scale the daily sending quota accurately.
2. Follow-ups (Step 2, 3, 4) take priority and deduct from the new outreach capacity.
3. Leftover personalized/approved leads from previous days are counted and deduct from today's deficit.
4. Only the exact deficit is personalized from the pool, leaving thousands of leads safe in the DB.
5. Worker integration executes JIT cycles and schedules dispatches seamlessly.
"""

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.mailbox import Mailbox
from app.models.setting import Setting
from app.services.fetcher import FakeFetcher
from app.services.jit_planner import get_jit_status, run_jit_batch
from app.services.llm import FakeLlm
from app.services.templates import load_default_sequence


@pytest.fixture
def jit_db() -> tuple[Session, Campaign]:
    """In-memory database with an active campaign and default sequence loaded."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sm() as session:
        campaign = Campaign(
            name="JIT Campaign",
            niche="software",
            status=CampaignStatus.ACTIVE.value,
        )
        session.add(campaign)
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-10-01"))
        session.add(Setting(key="MAILBOX_TZ", value="UTC"))
        session.add(Setting(key="AUTO_APPROVE", value="true"))
        session.add(Setting(key="SEND_DAYS", value='["Mon", "Tue", "Wed", "Thu", "Fri"]'))
        session.commit()
        session.refresh(campaign)

        # Load standard 4-step sequence
        load_default_sequence(campaign.id, session)

        yield session, campaign


def test_jit_multi_mailbox_capacity_calculation(jit_db: tuple[Session, Campaign]) -> None:
    """10 active mailboxes with daily_send_cap=30 produce a 300 emails/day quota."""
    session, campaign = jit_db
    clock = FakeClock(datetime(2026, 10, 16, 9, 30, 0, tzinfo=UTC))

    # Add 10 sender mailboxes
    for i in range(1, 11):
        mb = Mailbox(
            email=f"sender{i}@company.com",
            smtp_username=f"sender{i}@company.com",
            smtp_password="password",
            imap_username=f"sender{i}@company.com",
            imap_password="password",
            daily_send_cap=30,
            is_active=True,
        )
        session.add(mb)

    # Add 500 leads in the unprocessed pool
    pool_leads = [
        Lead(
            campaign_id=campaign.id,
            email=f"lead{i}@domain.com",
            company=f"Company {i}",
            status=LeadStatus.VALID.value,
        )
        for i in range(1, 501)
    ]
    session.add_all(pool_leads)
    session.commit()

    status = get_jit_status(db=session, target_date=date(2026, 10, 16), clock=clock)

    assert status.total_daily_cap == 300  # 10 accounts x 30
    assert status.remaining_daily_capacity == 300
    assert status.due_followups_count == 0
    assert status.available_new_slots == 300
    assert status.ready_leads_in_buffer == 0
    assert status.leads_needed_today == 300
    assert status.unprocessed_pool_count == 500


def test_jit_deducts_due_followups_and_leftover_ready_leads(
    jit_db: tuple[Session, Campaign],
) -> None:
    """Follow-ups and leftover personalized leads deduct from today's personalization quota."""
    session, campaign = jit_db
    clock = FakeClock(datetime(2026, 10, 16, 9, 30, 0, tzinfo=UTC))

    # 2 active mailboxes -> 60 daily cap
    mb1 = Mailbox(
        email="mb1@test.com",
        smtp_username="mb1",
        smtp_password="p",
        imap_username="mb1",
        imap_password="p",
        daily_send_cap=30,
        is_active=True,
    )
    mb2 = Mailbox(
        email="mb2@test.com",
        smtp_username="mb2",
        smtp_password="p",
        imap_username="mb2",
        imap_password="p",
        daily_send_cap=30,
        is_active=True,
    )
    session.add_all([mb1, mb2])
    session.commit()

    # Lead 1: has Step 1 sent 5 days ago -> Step 2 follow-up is due today!
    lead_followup = Lead(
        campaign_id=campaign.id,
        email="followup@target.com",
        company="Target Corp",
        status=LeadStatus.IN_SEQUENCE.value,
    )
    session.add(lead_followup)
    session.commit()

    sent_step1 = EmailMessage(
        lead_id=lead_followup.id,
        step=1,
        status=EmailMessageStatus.SENT.value,
        sent_at=datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC),
        subject="Step 1",
        body="Body 1",
    )
    due_step2 = EmailMessage(
        lead_id=lead_followup.id,
        step=2,
        status=EmailMessageStatus.APPROVED.value,
        subject="Re: Step 1",
        body="Body 2",
    )
    session.add_all([sent_step1, due_step2])

    # Lead 2 & 3: Already personalized & approved yesterday (leftover buffer)
    leftover_lead1 = Lead(
        campaign_id=campaign.id,
        email="leftover1@test.com",
        company="Leftover 1",
        personal_note="Grounded note 1",
        status=LeadStatus.APPROVED.value,
    )
    leftover_lead2 = Lead(
        campaign_id=campaign.id,
        email="leftover2@test.com",
        company="Leftover 2",
        personal_note="Grounded note 2",
        status=LeadStatus.APPROVED.value,
    )
    session.add_all([leftover_lead1, leftover_lead2])

    # 100 unprocessed leads in pool
    unprocessed = [
        Lead(
            campaign_id=campaign.id,
            email=f"pool{i}@pool.com",
            company=f"Pool {i}",
            status=LeadStatus.VALID.value,
        )
        for i in range(1, 101)
    ]
    session.add_all(unprocessed)
    session.commit()

    status = get_jit_status(db=session, target_date=date(2026, 10, 16), clock=clock)

    assert status.total_daily_cap == 60
    assert status.due_followups_count == 1  # 1 follow-up due
    assert status.available_new_slots == 59  # 60 - 1 = 59
    assert status.ready_leads_in_buffer == 2  # 2 leftover approved leads
    # Exact deficit needed = 59 - 2 = 57 leads!
    assert status.leads_needed_today == 57


def test_run_jit_batch_processes_only_deficit_leads(jit_db: tuple[Session, Campaign]) -> None:
    """run_jit_batch personalizes only the needed count from pool and leaves rest untouched."""
    session, campaign = jit_db
    clock = FakeClock(datetime(2026, 10, 16, 9, 30, 0, tzinfo=UTC))

    # Single mailbox with 5 daily cap
    mb = Mailbox(
        email="outreach@agency.com",
        smtp_username="user",
        smtp_password="pw",
        imap_username="user",
        imap_password="pw",
        daily_send_cap=5,
        is_active=True,
    )
    session.add(mb)

    # 20 leads in pool
    leads = [
        Lead(
            campaign_id=campaign.id,
            email=f"candidate{i}@target.com",
            company=f"Candidate {i}",
            website=None,  # Will pitch website
            status=LeadStatus.VALID.value,
        )
        for i in range(1, 21)
    ]
    session.add_all(leads)
    session.commit()

    fetcher = FakeFetcher()
    llm = FakeLlm()

    result = run_jit_batch(
        db=session,
        target_date=date(2026, 10, 16),
        clock=clock,
        fetcher=fetcher,
        llm=llm,
    )

    # Exactly 5 leads must be personalized to fill the 5 daily cap
    assert result.personalized_count == 5
    assert result.enrolled_count == 5

    # Exactly 15 leads must remain untouched in the pool
    remaining_pool = session.scalars(
        select(Lead).where(
            Lead.campaign_id == campaign.id,
            Lead.status == LeadStatus.VALID.value,
        )
    ).all()
    assert len(remaining_pool) == 15

    # 5 leads must be in APPROVED or IN_SEQUENCE status
    personalized = session.scalars(
        select(Lead).where(
            Lead.campaign_id == campaign.id,
            Lead.status.in_([LeadStatus.APPROVED.value, LeadStatus.IN_SEQUENCE.value]),
        )
    ).all()
    assert len(personalized) == 5


def test_reconcile_and_refresh_daily_schedule_carries_over_yesterday_leftovers(
    jit_db: tuple[Session, Campaign],
) -> None:
    """Pre-window refresh carries over yesterday's unsent emails, slots followups, and fills deficit."""
    from app.services.jit_planner import reconcile_and_refresh_daily_schedule

    session, campaign = jit_db
    # 08:58 AM (2 minutes before 09:00 AM window starts)
    clock = FakeClock(datetime(2026, 10, 16, 8, 58, 0, tzinfo=UTC))

    # Single mailbox with 10 daily cap
    mb = Mailbox(
        email="sender@agency.com",
        smtp_username="user",
        smtp_password="pw",
        imap_username="user",
        imap_password="pw",
        daily_send_cap=10,
        is_active=True,
    )
    session.add(mb)

    # 1. Leftover email from yesterday that didn't send before window closed
    lead_leftover = Lead(
        campaign_id=campaign.id,
        email="leftover@target.com",
        company="Leftover Corp",
        status=LeadStatus.IN_SEQUENCE.value,
        personal_note="Grounded note",
    )
    session.add(lead_leftover)
    session.commit()

    yesterday_email = EmailMessage(
        lead_id=lead_leftover.id,
        step=1,
        status=EmailMessageStatus.SCHEDULED.value,
        scheduled_at=datetime(2026, 10, 15, 11, 20, 0, tzinfo=UTC),  # Scheduled yesterday
        subject="Step 1",
        body="Body 1",
    )
    session.add(yesterday_email)

    # 2. Lead with follow-up due today
    lead_fu = Lead(
        campaign_id=campaign.id,
        email="fu@target.com",
        company="FU Corp",
        status=LeadStatus.IN_SEQUENCE.value,
    )
    session.add(lead_fu)
    session.commit()

    fu_sent_step1 = EmailMessage(
        lead_id=lead_fu.id,
        step=1,
        status=EmailMessageStatus.SENT.value,
        sent_at=datetime(2026, 10, 11, 10, 0, 0, tzinfo=UTC),
        subject="Previous Step",
        body="Prev Body",
    )
    fu_due_step2 = EmailMessage(
        lead_id=lead_fu.id,
        step=2,
        status=EmailMessageStatus.APPROVED.value,
        subject="Re: Previous Step",
        body="Follow-up Body",
    )
    session.add_all([fu_sent_step1, fu_due_step2])

    # 3. 20 leads in pool
    pool_leads = [
        Lead(
            campaign_id=campaign.id,
            email=f"pool_candidate{i}@target.com",
            company=f"Candidate {i}",
            status=LeadStatus.VALID.value,
        )
        for i in range(1, 21)
    ]
    session.add_all(pool_leads)
    session.commit()

    fetcher = FakeFetcher()
    llm = FakeLlm()

    # Run the pre-window reconciliation routine right at 08:58 AM
    result = reconcile_and_refresh_daily_schedule(
        db=session,
        target_date=date(2026, 10, 16),
        clock=clock,
        fetcher=fetcher,
        llm=llm,
    )

    # Daily cap is 10:
    # 1 followup + 1 leftover carried over = 2 slots used.
    # Deficit to personalize from pool = 10 - 2 = 8 leads!
    assert result.personalized_count == 8
    assert "Reconciled 1 leftover email(s)" in result.message

    # Verify yesterday's email was carried over and re-scheduled for TODAY (2026-10-16)
    session.refresh(yesterday_email)
    assert yesterday_email.status == EmailMessageStatus.SCHEDULED.value
    assert yesterday_email.scheduled_at.date() == date(2026, 10, 16)
    assert yesterday_email.scheduled_at.time() >= datetime.strptime("09:00", "%H:%M").time()

    # Total scheduled emails for today must be exactly 10 (hitting the daily cap without exceeding)
    today_scheduled = session.scalars(
        select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
    ).all()
    assert len(today_scheduled) == 10


def test_is_daily_refresh_due_timing_gate(jit_db: tuple[Session, Campaign]) -> None:
    """Pre-window trigger activates 1-2 mins before window start (08:58 AM), not early."""
    from app.services.jit_planner import is_daily_refresh_due, reconcile_and_refresh_daily_schedule

    session, campaign = jit_db
    # Settings: window 09:00 - 11:30
    session.add(Setting(key="SEND_WINDOW_START", value="09:00"))
    session.add(Setting(key="SEND_WINDOW_END", value="11:30"))
    session.commit()

    # 1. At 08:30 AM (too early, 30 mins before window): Should NOT trigger
    early_clock = FakeClock(datetime(2026, 10, 16, 8, 30, 0, tzinfo=UTC))
    assert is_daily_refresh_due(session, clock=early_clock, pre_window_minutes=2) is False

    # 2. At 08:58 AM (exactly 2 mins before 09:00 AM window): MUST trigger!
    trigger_clock = FakeClock(datetime(2026, 10, 16, 8, 58, 0, tzinfo=UTC))
    assert is_daily_refresh_due(session, clock=trigger_clock, pre_window_minutes=2) is True

    # 3. Once reconcile_and_refresh_daily_schedule runs, today is recorded
    reconcile_and_refresh_daily_schedule(
        db=session,
        target_date=date(2026, 10, 16),
        clock=trigger_clock,
    )
    # Next check today (e.g. at 09:05 AM) should be False because today has already been planned!
    mid_clock = FakeClock(datetime(2026, 10, 16, 9, 5, 0, tzinfo=UTC))
    assert is_daily_refresh_due(session, clock=mid_clock, pre_window_minutes=2) is False

    # 4. On Sunday (2026-10-18): Should NOT trigger because Sunday is not an outreach day
    sunday_clock = FakeClock(datetime(2026, 10, 18, 8, 58, 0, tzinfo=UTC))
    assert is_daily_refresh_due(session, clock=sunday_clock, pre_window_minutes=2) is False
