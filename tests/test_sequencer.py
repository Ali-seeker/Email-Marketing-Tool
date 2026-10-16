"""Unit and randomized state-machine tests for lead sequencer service.

Validates the complete lifecycle:
- Full happy path with FakeClock advancing across 4 steps and 7-day completion wait.
- Reply after Step 2 cancels Steps 3 and 4.
- Bounce after Step 1 cancels subsequent steps.
- Mid-sequence unsubscribe.
- Auto-reply delays follow-up by 5 days without stopping sequence.
- Soft-bounce single retry after 24 hours.
- Campaign pause/resume shifts due dates correctly.
- Central can_send() invariant holds across 500 randomized lifecycle events.
- Enrollment idempotency and linter-blocked lead handling.
"""

import random
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus
from app.models.suppression import Suppression
from app.services.sequencer import (
    can_send,
    cancel_lead_sequence,
    check_completed_leads,
    enroll,
    mark_not_interested,
    mark_replied,
    mark_won,
    on_auto_reply,
    on_email_sent,
    on_soft_bounce,
    pause_campaign,
    pause_lead,
    resume_campaign,
    resume_lead,
    skip_step,
)
from app.services.suppression import add as add_suppression
from app.services.templates import load_default_sequence


@pytest.fixture
def seq_db() -> tuple[Session, sessionmaker]:
    """Provide an isolated in-memory SQLite database for sequencer testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = factory()

    # Create default active campaign
    campaign = Campaign(name="Sequencer Test Campaign", status=CampaignStatus.ACTIVE.value)
    session.add(campaign)
    session.flush()

    # Load sequence steps for this campaign
    load_default_sequence(campaign.id, session)

    session.commit()
    try:
        yield session, factory
    finally:
        session.close()


def _create_approved_lead(
    db: Session,
    campaign_id: int,
    email: str,
    first_name: str = "Alice",
    company: str = "Wonderland SaaS",
    personal_note: str = "Loved your recent engineering blog on distributed systems.",
) -> Lead:
    """Helper to seed a lead in approved status."""
    lead = Lead(
        campaign_id=campaign_id,
        email=email.lower().strip(),
        first_name=first_name,
        company=company,
        personal_note=personal_note,
        status=LeadStatus.APPROVED.value,
        timezone="America/New_York",
    )
    db.add(lead)
    db.commit()
    db.refresh(lead)
    return lead


def test_full_happy_path_three_leads_and_timeline(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify 3 leads progress through all 4 steps and complete after 7 days."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    t0 = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
    clock = FakeClock(t0)

    leads = [
        _create_approved_lead(db, campaign.id, f"lead{i}@acme.com", first_name=f"User{i}")
        for i in range(1, 4)
    ]

    # 1. Enroll leads
    enrolled = enroll(campaign, db, clock=clock)
    assert len(enrolled) == 3

    timeline_log: list[str] = [f"[{clock.now().isoformat()}] Enrolled 3 leads into sequence"]

    for lead in leads:
        db.refresh(lead)
        assert lead.status == LeadStatus.IN_SEQUENCE.value
        msgs = db.scalars(
            select(EmailMessage)
            .where(EmailMessage.lead_id == lead.id)
            .order_by(EmailMessage.step.asc())
        ).all()
        assert len(msgs) == 4
        assert msgs[0].status == EmailMessageStatus.APPROVED.value
        assert msgs[1].status == EmailMessageStatus.DRAFT.value
        assert msgs[2].status == EmailMessageStatus.DRAFT.value
        assert msgs[3].status == EmailMessageStatus.DRAFT.value

    # STEP 1: Dispatch immediately on Day 0
    timeline_log.append(f"[{clock.now().isoformat()}] Dispatching Step 1 for all leads")
    for lead in leads:
        msg1 = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
        )
        assert msg1 is not None
        assert can_send(lead, msg1, db, clock=clock) is True

        # Simulate send
        msg1.status = EmailMessageStatus.SENT.value
        msg1.sent_at = clock.now()
        msg1.message_id = f"<msg-step1-{lead.id}@studio.com>"
        db.commit()
        next_msg = on_email_sent(msg1, db, clock=clock)
        assert next_msg is not None
        assert next_msg.step == 2
        assert next_msg.status == EmailMessageStatus.APPROVED.value
        assert next_msg.earliest_send_at == clock.now() + timedelta(days=4)

    # Day 2 check: Step 2 should NOT be sendable yet
    clock.advance(timedelta(days=2))
    for lead in leads:
        msg2 = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
        )
        assert can_send(lead, msg2, db, clock=clock) is False

    # STEP 2: Advance to Day 4 (+4 days from Step 1)
    clock.advance(timedelta(days=2))
    timeline_log.append(f"[{clock.now().isoformat()}] Day 4: Dispatching Step 2 for all leads")
    for lead in leads:
        msg2 = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
        )
        assert can_send(lead, msg2, db, clock=clock) is True

        msg2.status = EmailMessageStatus.SENT.value
        msg2.sent_at = clock.now()
        msg2.message_id = f"<msg-step2-{lead.id}@studio.com>"
        db.commit()
        next_msg = on_email_sent(msg2, db, clock=clock)
        assert next_msg is not None
        assert next_msg.step == 3
        assert next_msg.status == EmailMessageStatus.APPROVED.value
        assert next_msg.earliest_send_at == clock.now() + timedelta(days=4)

    # STEP 3: Advance to Day 8 (+4 days from Step 2)
    clock.advance(timedelta(days=4))
    timeline_log.append(f"[{clock.now().isoformat()}] Day 8: Dispatching Step 3 for all leads")
    for lead in leads:
        msg3 = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 3)
        )
        assert can_send(lead, msg3, db, clock=clock) is True

        msg3.status = EmailMessageStatus.SENT.value
        msg3.sent_at = clock.now()
        msg3.message_id = f"<msg-step3-{lead.id}@studio.com>"
        db.commit()
        next_msg = on_email_sent(msg3, db, clock=clock)
        assert next_msg is not None
        assert next_msg.step == 4
        assert next_msg.status == EmailMessageStatus.APPROVED.value
        assert next_msg.earliest_send_at == clock.now() + timedelta(days=6)

    # STEP 4: Advance to Day 14 (+6 days from Step 3)
    clock.advance(timedelta(days=6))
    timeline_log.append(f"[{clock.now().isoformat()}] Day 14: Dispatching Step 4 for all leads")
    for lead in leads:
        msg4 = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 4)
        )
        assert can_send(lead, msg4, db, clock=clock) is True

        msg4.status = EmailMessageStatus.SENT.value
        msg4.sent_at = clock.now()
        msg4.message_id = f"<msg-step4-{lead.id}@studio.com>"
        db.commit()
        res = on_email_sent(msg4, db, clock=clock)
        assert res is None  # No step 5

    # Day 18 check: Step 4 was sent 4 days ago; lead still in sequence (needs 7 days)
    clock.advance(timedelta(days=4))
    completed_early = check_completed_leads(db, clock=clock)
    assert len(completed_early) == 0
    for lead in leads:
        db.refresh(lead)
        assert lead.status == LeadStatus.IN_SEQUENCE.value

    # Day 21 check: 7 full days after Step 4 sent -> leads complete!
    clock.advance(timedelta(days=3))
    timeline_log.append(f"[{clock.now().isoformat()}] Day 21: Sequence completed with no replies")
    completed = check_completed_leads(db, clock=clock)
    assert len(completed) == 3
    for lead in leads:
        db.refresh(lead)
        assert lead.status == LeadStatus.COMPLETED.value


def test_reply_after_step_2_cancels_steps_3_and_4(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify receiving a reply after step 2 cancels remaining steps and blocks dispatches."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "replier@acme.com")
    enroll(campaign, db, clock=clock)

    # Send Step 1
    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert msg1 is not None
    msg1.status = EmailMessageStatus.SENT.value
    msg1.sent_at = clock.now()
    on_email_sent(msg1, db, clock=clock)

    # Fast forward 4 days and send Step 2
    clock.advance(timedelta(days=4))
    msg2 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
    )
    assert msg2 is not None
    msg2.status = EmailMessageStatus.SENT.value
    msg2.sent_at = clock.now()
    on_email_sent(msg2, db, clock=clock)

    msg3 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 3)
    )
    msg4 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 4)
    )
    assert msg3 is not None and msg3.status == EmailMessageStatus.APPROVED.value
    assert msg4 is not None and msg4.status == EmailMessageStatus.DRAFT.value

    # Reply arrives
    lead.status = LeadStatus.REPLIED.value
    cancel_lead_sequence(lead.id, "reply received", db, clock=clock)
    db.refresh(lead)
    db.refresh(msg3)
    db.refresh(msg4)

    assert lead.status == LeadStatus.REPLIED.value
    assert msg3.status == EmailMessageStatus.SKIPPED.value
    assert msg4.status == EmailMessageStatus.SKIPPED.value
    assert "reply received" in (msg3.error or "")

    # Zero emails sendable for this replied lead
    assert can_send(lead, msg3, db, clock=clock) is False
    assert can_send(lead, msg4, db, clock=clock) is False


def test_bounce_after_step_1_cancels_rest(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify hard bounce after step 1 cancels steps 2-4 and suppresses the lead."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "bouncer@acme.com")
    enroll(campaign, db, clock=clock)

    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert msg1 is not None
    msg1.status = EmailMessageStatus.SENT.value
    msg1.sent_at = clock.now()
    on_email_sent(msg1, db, clock=clock)

    # Bounce happens
    add_suppression(db, target=lead.email, reason="bounce", clock=clock)
    cancel_lead_sequence(lead.id, "hard bounce", db, clock=clock)

    db.refresh(lead)
    assert lead.status == LeadStatus.BOUNCED.value

    # Check steps 2, 3, 4 cancelled
    for s in (2, 3, 4):
        msg_s = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == s)
        )
        assert msg_s is not None
        assert msg_s.status == EmailMessageStatus.SKIPPED.value
        assert can_send(lead, msg_s, db, clock=clock) is False


def test_unsubscribe_mid_sequence(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify opt-out cancels pending sequence messages and suppresses the lead."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "optout@acme.com")
    enroll(campaign, db, clock=clock)

    # Lead requests removal before Step 1 is sent
    mark_not_interested(lead.id, db, clock=clock)
    db.refresh(lead)

    assert lead.status == LeadStatus.UNSUBSCRIBED.value
    supp = db.scalar(select(Suppression).where(Suppression.email == "optout@acme.com"))
    assert supp is not None

    for s in (1, 2, 3, 4):
        msg = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == s)
        )
        assert msg is not None
        assert msg.status == EmailMessageStatus.SKIPPED.value
        assert can_send(lead, msg, db, clock=clock) is False


def test_auto_reply_delays_next_step_by_five_days(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify out-of-office auto-reply shifts follow-up due date by 5 days without stopping."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    t0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    clock = FakeClock(t0)
    lead = _create_approved_lead(db, campaign.id, "vacation@acme.com")
    enroll(campaign, db, clock=clock)

    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert msg1 is not None
    msg1.status = EmailMessageStatus.SENT.value
    msg1.sent_at = clock.now()
    on_email_sent(msg1, db, clock=clock)

    msg2 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
    )
    assert msg2 is not None
    orig_due = msg2.earliest_send_at
    assert orig_due == t0 + timedelta(days=4)

    # Auto-reply arrives 1 day after Step 1 was sent
    clock.advance(timedelta(days=1))
    on_auto_reply(lead.id, db, clock=clock, delay_days=5)
    db.refresh(msg2)
    db.refresh(lead)

    # Sequence stays active
    assert lead.status == LeadStatus.IN_SEQUENCE.value
    # Earliest send pushed forward by 5 days (from Day 4 to Day 9)
    assert msg2.earliest_send_at == orig_due + timedelta(days=5)

    # On Day 4: Not sendable yet!
    clock.advance(timedelta(days=3))
    assert clock.now() == t0 + timedelta(days=4)
    assert can_send(lead, msg2, db, clock=clock) is False

    # On Day 9: Sendable!
    clock.advance(timedelta(days=5))
    assert clock.now() == t0 + timedelta(days=9)
    assert can_send(lead, msg2, db, clock=clock) is True


def test_soft_bounce_retry_once(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify soft bounce is retried once after 24 hours, then fails on subsequent bounce."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    t0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    clock = FakeClock(t0)
    lead = _create_approved_lead(db, campaign.id, "soft@acme.com")
    enroll(campaign, db, clock=clock)

    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert msg1 is not None

    # First soft bounce: retried once after 24 hours
    retried = on_soft_bounce(msg1, db, clock=clock)
    assert retried is True
    assert msg1.status == EmailMessageStatus.APPROVED.value
    assert msg1.retry_count == 1
    assert msg1.earliest_send_at == t0 + timedelta(hours=24)

    # Advance 12 hours: Not sendable yet
    clock.advance(timedelta(hours=12))
    assert can_send(lead, msg1, db, clock=clock) is False

    # Advance 12 more hours: Due and sendable
    clock.advance(timedelta(hours=12))
    assert can_send(lead, msg1, db, clock=clock) is True

    # Second soft bounce on the retry attempt: fails!
    retried_again = on_soft_bounce(msg1, db, clock=clock)
    assert retried_again is False
    assert msg1.status == EmailMessageStatus.FAILED.value
    assert can_send(lead, msg1, db, clock=clock) is False


def test_pause_and_resume_campaign_shifts_due_dates(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify pausing a campaign pauses dispatches and resumes by shifting due dates."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    t0 = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
    clock = FakeClock(t0)
    lead = _create_approved_lead(db, campaign.id, "paused_camp@acme.com")
    enroll(campaign, db, clock=clock)

    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert msg1 is not None
    msg1.status = EmailMessageStatus.SENT.value
    msg1.sent_at = clock.now()
    on_email_sent(msg1, db, clock=clock)

    msg2 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
    )
    assert msg2 is not None
    orig_due = msg2.earliest_send_at
    assert orig_due == t0 + timedelta(days=4)

    # Advance 1 day and pause campaign
    clock.advance(timedelta(days=1))
    pause_campaign(campaign.id, db, clock=clock)
    db.refresh(campaign)
    assert campaign.status == CampaignStatus.PAUSED.value

    # While campaign is paused, can_send is strictly False
    assert can_send(lead, msg2, db, clock=clock) is False

    # Advance 3 days during pause (pause duration = 3 days)
    clock.advance(timedelta(days=3))
    assert can_send(lead, msg2, db, clock=clock) is False

    # Resume campaign
    resume_campaign(campaign.id, db, clock=clock)
    db.refresh(campaign)
    db.refresh(msg2)
    assert campaign.status == CampaignStatus.ACTIVE.value

    # Due date was shifted by exactly 3 days (pause duration)
    assert msg2.earliest_send_at == orig_due + timedelta(days=3)


def test_enrollment_idempotency_and_linter_blocked(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify enrolling twice never duplicates messages and linter blocks spammy Step 1."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))

    # Lead 1: valid note
    lead1 = _create_approved_lead(
        db,
        campaign.id,
        "clean@acme.com",
        personal_note="Impressed by your team's open source contributions.",
    )

    # Lead 2: spammy personal note designed to trigger the spam linter
    lead2 = _create_approved_lead(
        db,
        campaign.id,
        "spammy@acme.com",
        personal_note="$$$ BUY NOW 100% FREE GUARANTEED CASH VIAGRA RISK-FREE ACT NOW $$$",
    )

    # First enrollment
    enrolled = enroll(campaign, db, clock=clock)
    assert lead1 in enrolled
    assert lead2 not in enrolled  # Blocked by linter!

    db.refresh(lead1)
    db.refresh(lead2)
    assert lead1.status == LeadStatus.IN_SEQUENCE.value
    assert lead2.status == LeadStatus.APPROVED.value  # Remained blocked
    assert "Linter blocked Step 1" in (lead2.validation_reason or "")

    # Message 1 for clean lead is approved
    clean_msgs = db.scalars(select(EmailMessage).where(EmailMessage.lead_id == lead1.id)).all()
    assert len(clean_msgs) == 4

    # Message 1 for spammy lead is needs_review, no subsequent steps created
    spam_msgs = db.scalars(select(EmailMessage).where(EmailMessage.lead_id == lead2.id)).all()
    assert len(spam_msgs) == 1
    assert spam_msgs[0].status == EmailMessageStatus.NEEDS_REVIEW.value
    assert can_send(lead2, spam_msgs[0], db, clock=clock) is False

    # Re-enrolling twice creates ZERO duplicates
    enrolled_again = enroll(campaign, db, clock=clock)
    assert len(enrolled_again) == 0
    all_msgs = db.scalars(select(EmailMessage)).all()
    assert len(all_msgs) == 5  # 4 for lead1 + 1 for lead2


def test_manual_actions(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify manual operator overrides: pause/resume, won, skip step."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "operator@acme.com")
    enroll(campaign, db, clock=clock)

    # 1. Manual Pause & Resume
    pause_lead(lead.id, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.PAUSED.value
    msg1 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 1)
    )
    assert can_send(lead, msg1, db, clock=clock) is False

    clock.advance(timedelta(days=2))
    resume_lead(lead.id, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.APPROVED.value

    # 2. Skip Step 1
    skip_step(lead.id, 1, db, clock=clock)
    db.refresh(msg1)
    assert msg1.status == EmailMessageStatus.SKIPPED.value

    # Step 2 promoted to approved
    msg2 = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
    )
    assert msg2 is not None
    assert msg2.status == EmailMessageStatus.APPROVED.value

    # 3. Mark Won
    mark_won(lead.id, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.WON.value

    # Remaining messages cancelled
    for s in (2, 3, 4):
        msg_s = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == s)
        )
        assert msg_s.status == EmailMessageStatus.SKIPPED.value
        assert can_send(lead, msg_s, db, clock=clock) is False


def test_randomized_state_machine_guarantee(seq_db: tuple[Session, sessionmaker]) -> None:
    """Run 500 randomized lifecycle events across 50 leads asserting central safety invariant."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 9, 0, tzinfo=UTC))
    rng = random.Random(42)

    # Create and enroll 50 leads
    leads = [
        _create_approved_lead(
            db,
            campaign.id,
            f"rand_lead_{i}@enterprise.com",
            first_name=f"User{i}",
            personal_note=f"Observed key architecture note #{i} in production.",
        )
        for i in range(50)
    ]
    enroll(campaign, db, clock=clock)

    event_types = [
        "send_due_email",
        "reply",
        "hard_bounce",
        "soft_bounce",
        "unsubscribe",
        "auto_reply",
        "pause_lead",
        "resume_lead",
        "pause_campaign",
        "resume_campaign",
        "advance_clock",
        "check_completed",
    ]

    terminal_statuses = (
        LeadStatus.REPLIED.value,
        LeadStatus.BOUNCED.value,
        LeadStatus.UNSUBSCRIBED.value,
        LeadStatus.COMPLETED.value,
        LeadStatus.INVALID.value,
        LeadStatus.PAUSED.value,
    )

    for step_iteration in range(500):
        action = rng.choice(event_types)
        lead = rng.choice(leads)
        db.refresh(lead)

        if action == "advance_clock":
            hours = rng.randint(6, 48)
            clock.advance(timedelta(hours=hours))

        elif action == "check_completed":
            check_completed_leads(db, clock=clock)

        elif action == "send_due_email":
            # Attempt to find any sendable email for this lead
            all_lead_msgs = db.scalars(
                select(EmailMessage)
                .where(EmailMessage.lead_id == lead.id)
                .order_by(EmailMessage.step.asc())
            ).all()
            for msg in all_lead_msgs:
                if can_send(lead, msg, db, clock=clock):
                    msg.status = EmailMessageStatus.SENT.value
                    msg.sent_at = clock.now()
                    msg.message_id = f"<rand-{lead.id}-{msg.step}@studio.com>"
                    db.commit()
                    on_email_sent(msg, db, clock=clock)
                    break

        elif action == "reply":
            if lead.status == LeadStatus.IN_SEQUENCE.value:
                mark_replied(lead.id, db, clock=clock)

        elif action == "hard_bounce":
            if lead.status in (LeadStatus.IN_SEQUENCE.value, LeadStatus.APPROVED.value):
                add_suppression(db, target=lead.email, reason="bounce", clock=clock)
                cancel_lead_sequence(lead.id, "hard bounce", db, clock=clock)

        elif action == "unsubscribe":
            if lead.status in (LeadStatus.IN_SEQUENCE.value, LeadStatus.APPROVED.value):
                mark_not_interested(lead.id, db, clock=clock)

        elif action == "soft_bounce":
            if lead.status == LeadStatus.IN_SEQUENCE.value:
                cur_msg = db.scalar(
                    select(EmailMessage).where(
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.status == EmailMessageStatus.APPROVED.value,
                    )
                )
                if cur_msg:
                    on_soft_bounce(cur_msg, db, clock=clock)

        elif action == "auto_reply":
            if lead.status == LeadStatus.IN_SEQUENCE.value:
                on_auto_reply(lead.id, db, clock=clock, delay_days=5)

        elif action == "pause_lead":
            if lead.status == LeadStatus.IN_SEQUENCE.value:
                pause_lead(lead.id, db, clock=clock)

        elif action == "resume_lead":
            if lead.status == LeadStatus.PAUSED.value:
                resume_lead(lead.id, db, clock=clock)

        elif action == "pause_campaign":
            db.refresh(campaign)
            if campaign.status == CampaignStatus.ACTIVE.value:
                pause_campaign(campaign.id, db, clock=clock)

        elif action == "resume_campaign":
            db.refresh(campaign)
            if campaign.status == CampaignStatus.PAUSED.value:
                resume_campaign(campaign.id, db, clock=clock)

        # =====================================================================
        # CENTRAL INVARIANT ASSERTION (Asserted after every single event)
        # =====================================================================
        # Check affected lead after every event
        db.refresh(lead)
        affected_lead_msgs = db.scalars(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id)
        ).all()
        is_stopped = (
            lead.status in terminal_statuses
            or db.scalar(select(Suppression).where(Suppression.email == lead.email)) is not None
        )

        if is_stopped:
            for m in affected_lead_msgs:
                assert can_send(lead, m, db, clock=clock) is False, (
                    f"CRITICAL VIOLATION on step {step_iteration}: "
                    f"Lead {lead.email} in status {lead.status} "
                    f"returned can_send=True for step {m.step} (msg status {m.status})!"
                )

        # If campaign is paused, verify no message of affected lead is sendable
        db.refresh(campaign)
        if campaign.status == CampaignStatus.PAUSED.value:
            for m in affected_lead_msgs:
                assert can_send(lead, m, db, clock=clock) is False

        # Periodic and final exhaustive audit across ALL 50 leads
        if step_iteration % 50 == 0 or step_iteration == 499:
            suppressed_set = set(db.scalars(select(Suppression.email)).all())
            all_leads = db.scalars(select(Lead).where(Lead.campaign_id == campaign.id)).all()
            for sample_lead in all_leads:
                lead_is_stopped = (
                    sample_lead.status in terminal_statuses or sample_lead.email in suppressed_set
                )
                if lead_is_stopped:
                    msgs = db.scalars(
                        select(EmailMessage).where(EmailMessage.lead_id == sample_lead.id)
                    ).all()
                    for m in msgs:
                        assert can_send(sample_lead, m, db, clock=clock) is False


def test_mark_not_interested_and_check_completed_leads(
    seq_db: tuple[Session, sessionmaker],
) -> None:
    """Verify mark_not_interested suppresses and check_completed_leads marks finished sequences."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    lead1 = _create_approved_lead(db, campaign.id, "notinterested@acme.com")
    lead2 = _create_approved_lead(db, campaign.id, "completed@acme.com")
    enroll(campaign, db, clock=clock)

    # 1. mark_not_interested
    mark_not_interested(lead1.id, db, clock=clock)
    db.refresh(lead1)
    assert lead1.status == LeadStatus.UNSUBSCRIBED.value
    assert db.scalar(select(Suppression).where(Suppression.email == lead1.email)) is not None

    # 2. check_completed_leads
    # Mark all 4 steps of lead2 as sent 8 days ago
    eight_days_ago = clock.now() - timedelta(days=8)
    for s in (1, 2, 3, 4):
        msg = db.scalar(
            select(EmailMessage).where(EmailMessage.lead_id == lead2.id, EmailMessage.step == s)
        )
        msg.status = EmailMessageStatus.SENT.value
        msg.sent_at = eight_days_ago
    lead2.status = LeadStatus.IN_SEQUENCE.value
    db.commit()

    completed = check_completed_leads(db, clock=clock)
    assert lead2.id in [item.id for item in completed]
    db.refresh(lead2)
    assert lead2.status == LeadStatus.COMPLETED.value


def test_can_send_step_window_and_weekday_checks(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify can_send checks allowed weekdays and window times when status is SCHEDULED."""
    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    # Tuesday 10:00 (inside window and weekday)
    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "window_test@acme.com")
    lead.timezone = "UTC"
    db.commit()

    msg = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Hello",
        body="Body",
        status=EmailMessageStatus.SCHEDULED.value,
        scheduled_at=clock.now(),
    )
    db.add(msg)
    db.commit()

    assert can_send(lead, msg, db, clock=clock) is True

    # Advance to Sunday (outside weekday)
    sunday_clock = FakeClock(datetime(2026, 11, 8, 10, 0, tzinfo=UTC))
    assert can_send(lead, msg, db, clock=sunday_clock) is False

    # Tuesday 23:00 (outside window)
    night_clock = FakeClock(datetime(2026, 11, 3, 23, 0, tzinfo=UTC))
    assert can_send(lead, msg, db, clock=night_clock) is False


def test_sequencer_error_handling_and_not_found(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify sequencer raises appropriate errors for missing IDs."""
    db, _ = seq_db
    import pytest

    from app.services.sequencer import (
        pause_campaign,
        pause_lead,
        resume_campaign,
        resume_lead,
        skip_step,
    )

    with pytest.raises(ValueError, match="not found"):
        pause_campaign(99999, db)

    with pytest.raises(ValueError, match="not found"):
        resume_campaign(99999, db)

    with pytest.raises(ValueError, match="not found"):
        pause_lead(99999, db)

    with pytest.raises(ValueError, match="not found"):
        resume_lead(99999, db)

    with pytest.raises(ValueError, match="not found"):
        skip_step(99999, 1, db)


def test_can_send_comprehensive_safety_gates(seq_db: tuple[Session, sessionmaker]) -> None:
    """Verify all can_send safety gates and get_sequence_step_delay."""
    from app.models import SequenceStep
    from app.services.safety import trigger_killswitch
    from app.services.sequencer import get_step_offset_days

    db, _ = seq_db
    campaign = db.scalar(select(Campaign))
    assert campaign is not None

    clock = FakeClock(datetime(2026, 11, 3, 10, 0, tzinfo=UTC))
    lead = _create_approved_lead(db, campaign.id, "gates_test@acme.com")
    db.commit()

    msg1 = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Step 1",
        body="Body",
        status=EmailMessageStatus.APPROVED.value,
    )
    msg2 = EmailMessage(
        lead_id=lead.id,
        step=2,
        subject="Step 2",
        body="Body",
        status=EmailMessageStatus.APPROVED.value,
    )
    db.add_all([msg1, msg2])
    db.commit()

    # 1. None handling
    assert can_send(None, msg1, db, clock=clock) is False
    assert can_send(lead, None, db, clock=clock) is False

    # 2. Step 2 requires Step 1 to be sent
    assert can_send(lead, msg2, db, clock=clock) is False

    # 3. Earliest send at in future
    msg1.earliest_send_at = clock.now() + timedelta(days=2)
    assert can_send(lead, msg1, db, clock=clock) is False
    msg1.earliest_send_at = None

    # 4. Campaign not active
    campaign.status = CampaignStatus.PAUSED.value
    db.commit()
    assert can_send(lead, msg1, db, clock=clock) is False
    campaign.status = CampaignStatus.ACTIVE.value
    db.commit()

    # 5. Global killswitch active
    trigger_killswitch(db, "Test killswitch for gates", clock=clock)
    assert can_send(lead, msg1, db, clock=clock) is False

    # 6. Custom SequenceStep delay
    camp2 = Campaign(name="Custom Delay Campaign")
    db.add(camp2)
    db.commit()
    db.add(
        SequenceStep(
            campaign_id=camp2.id, step=2, delay_days=5, subjects=["Follow up"], bodies=["Body"]
        )
    )
    db.commit()
    assert get_step_offset_days(db, camp2.id, 2) == 5
