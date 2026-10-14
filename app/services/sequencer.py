"""Lead lifecycle state machine and sequence orchestrator.

Implements strict sequence progression and safety guarantees:
1. Enrollment: Renders 4-step sequence templates and lints Step 1.
2. Step progression: Advances to the next step with computed due offset on dispatch.
3. Cancellation: Immediately skips remaining messages on reply, bounce, or suppression.
4. Central Gatekeeper: can_send() guarantees zero emails sent to a stopped lead.
5. Pause/Resume: Shifts earliest due dates by paused duration without losing timing.
6. Auto-reply & Soft-bounce handling: Delays follow-up by 5 days or retries once.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.core.logging import get_logger
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.services.suppression import add as add_suppression
from app.services.suppression import is_suppressed
from app.services.templates import (
    load_default_sequence,
    render_step_email,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# Standard sequence step intervals in days (after the immediately preceding step was sent)
# Step 1: Day 0 (immediate)
# Step 2: Day 4 (+4 days after Step 1)
# Step 3: Day 8 (+4 days after Step 2)
# Step 4: Day 14 (+6 days after Step 3)
DEFAULT_STEP_DELAYS: dict[int, int] = {
    1: 0,
    2: 4,
    3: 4,
    4: 6,
}


def is_killswitch_active(db: Session) -> bool:
    """Check if emergency global kill-switch is active in Setting table."""
    val = db.scalar(select(Setting.value).where(Setting.key == "KILLSWITCH"))
    return bool(val is not None and val.strip().lower() in ("on", "true", "1", "yes"))


def get_step_offset_days(db: Session, campaign_id: int, step: int) -> int:
    """Calculate the required delay (in days) after the previous step was sent."""
    step_def = db.scalar(
        select(SequenceStep).where(
            SequenceStep.campaign_id == campaign_id,
            SequenceStep.step == step,
        )
    )
    if step_def:
        prev_step_def = db.scalar(
            select(SequenceStep).where(
                SequenceStep.campaign_id == campaign_id,
                SequenceStep.step == step - 1,
            )
        )
        if prev_step_def and step_def.delay_days > prev_step_def.delay_days:
            return step_def.delay_days - prev_step_def.delay_days
        elif step_def.delay_days > 0:
            return step_def.delay_days

    return DEFAULT_STEP_DELAYS.get(step, 4)


def can_send(
    lead: Lead | None,
    email: EmailMessage | None,
    db: Session,
    clock: Clock | None = None,
) -> bool:
    """Central safety gatekeeper enforcing zero email dispatches for stopped leads.

    Guarantees:
    - Lead must not be in terminal status (replied, bounced, unsubscribed,
      completed, invalid, paused).
    - Lead email must not be in global suppression.
    - Parent campaign must be in ACTIVE status.
    - Global killswitch must be inactive.
    - Email status must be sendable (approved, scheduled, sending).
    - Follow-up steps (>1) require that the immediately preceding step was SENT.
    - Email earliest_send_at must have arrived.
    """
    if lead is None or email is None:
        return False

    # 1. Lead lifecycle status check (must be approved or in_progress, never stopped or unapproved)
    stopped_statuses = (
        LeadStatus.REPLIED.value,
        LeadStatus.BOUNCED.value,
        LeadStatus.UNSUBSCRIBED.value,
        LeadStatus.COMPLETED.value,
        LeadStatus.INVALID.value,
        LeadStatus.PAUSED.value,
        LeadStatus.NEW.value,
    )
    if lead.status in stopped_statuses:
        return False

    # 1b. Step 1 requires an approved personal note
    if email.step == 1 and (not lead.personal_note or not lead.personal_note.strip()):
        return False

    # 2. Global suppression check
    if is_suppressed(db, lead.email):
        return False

    # 3. Campaign status check
    campaign = lead.campaign or db.get(Campaign, lead.campaign_id)
    if campaign is None or campaign.status != CampaignStatus.ACTIVE.value:
        return False

    # 4. Global emergency killswitch
    if is_killswitch_active(db):
        return False

    # 5. Email lifecycle status check
    non_sendable_statuses = (
        EmailMessageStatus.SKIPPED.value,
        EmailMessageStatus.SENT.value,
        EmailMessageStatus.SENT_DRY_RUN.value,
        EmailMessageStatus.FAILED.value,
        EmailMessageStatus.DRAFT.value,
        EmailMessageStatus.NEEDS_REVIEW.value,
    )
    if email.status in non_sendable_statuses:
        return False

    # 6. Sequence ordering: steps > 1 require previous step to be SENT
    if email.step > 1:
        prev_msg = db.scalar(
            select(EmailMessage).where(
                EmailMessage.lead_id == lead.id,
                EmailMessage.step == email.step - 1,
            )
        )
        if prev_msg is None:
            return False
        if prev_msg.status not in (
            EmailMessageStatus.SENT.value,
            EmailMessageStatus.SENT_DRY_RUN.value,
        ):
            return False

    # 7. Earliest send time constraint
    if email.earliest_send_at is not None:
        clk = clock or system_clock
        now = clk.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        earliest = email.earliest_send_at
        if earliest.tzinfo is None:
            earliest = earliest.replace(tzinfo=UTC)
        if now < earliest:
            return False

    # 8. Local window and allowed weekday constraint for outbound dispatches
    if email.status in (EmailMessageStatus.SCHEDULED.value, EmailMessageStatus.SENDING.value):
        from zoneinfo import ZoneInfo

        from app.services.planner import (
            get_allowed_weekdays,
            get_mailbox_tz,
            get_send_window,
        )

        clk = clock or system_clock
        now = clk.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        mailbox_tz = get_mailbox_tz(db)
        lead_tz = ZoneInfo(lead.timezone) if lead.timezone else mailbox_tz
        local_time = now.astimezone(lead_tz)
        allowed_weekdays = get_allowed_weekdays(db)
        if local_time.weekday() not in allowed_weekdays:
            return False
        w_start, w_end = get_send_window(db)
        if not (w_start <= local_time.time() <= w_end):
            return False

    return True


def enroll(
    campaign: Campaign | int,
    db: Session,
    clock: Clock | None = None,
) -> list[Lead]:
    """Enroll all approved leads in a campaign into the 4-step sequence.

    Args:
        campaign: Campaign instance or campaign ID.
        db: SQLAlchemy session.
        clock: Optional Clock implementation.

    Returns:
        List of successfully enrolled Lead instances.
    """
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    target_campaign = campaign if isinstance(campaign, Campaign) else db.get(Campaign, campaign)
    if not target_campaign:
        raise ValueError("Campaign not found")

    # Ensure sequence definitions exist for this campaign
    steps_count = db.scalar(
        select(SequenceStep).where(SequenceStep.campaign_id == target_campaign.id)
    )
    if not steps_count:
        load_default_sequence(target_campaign.id, db)

    # Find leads in 'approved' status
    stmt = select(Lead).where(
        Lead.campaign_id == target_campaign.id,
        Lead.status == LeadStatus.APPROVED.value,
    )
    candidate_leads = db.scalars(stmt).all()
    enrolled_leads: list[Lead] = []

    for lead in candidate_leads:
        # Idempotency check: if messages already exist for this lead, do not duplicate
        existing_msgs = db.scalars(
            select(EmailMessage).where(EmailMessage.lead_id == lead.id)
        ).all()
        if existing_msgs:
            continue

        # Render Step 1
        rendered_1 = render_step_email(lead, 1, db)

        # Linter check on Step 1
        if not rendered_1.lint_result.passed:
            lead.validation_reason = (
                f"Linter blocked Step 1: {'; '.join(rendered_1.lint_result.failures)}"
            )
            # Create Step 1 as needs_review so reason is inspectable, lead stays blocked
            msg_1 = EmailMessage(
                lead_id=lead.id,
                step=1,
                subject=rendered_1.subject,
                body=rendered_1.body,
                status=EmailMessageStatus.NEEDS_REVIEW.value,
                error=lead.validation_reason,
                subject_variant_index=rendered_1.subject_variant_index,
                body_variant_index=rendered_1.body_variant_index,
                offset_days=0,
                earliest_send_at=now,
            )
            db.add(msg_1)
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.ERROR.value,
                    payload={"reason": lead.validation_reason, "step": 1},
                    created_at=now,
                )
            )
            logger.warning(
                "Lead %s blocked by linter on Step 1: %s", lead.email, lead.validation_reason
            )
            continue

        # Step 1 passed linter: create Step 1 message as approved
        msg_1 = EmailMessage(
            lead_id=lead.id,
            step=1,
            subject=rendered_1.subject,
            body=rendered_1.body,
            status=EmailMessageStatus.APPROVED.value,
            subject_variant_index=rendered_1.subject_variant_index,
            body_variant_index=rendered_1.body_variant_index,
            offset_days=0,
            earliest_send_at=now,
        )
        db.add(msg_1)

        # Render Steps 2, 3, 4 as draft with computed offsets
        for s_num in (2, 3, 4):
            rendered_s = render_step_email(lead, s_num, db)
            offset = get_step_offset_days(db, target_campaign.id, s_num)
            msg_s = EmailMessage(
                lead_id=lead.id,
                step=s_num,
                subject=rendered_s.subject,
                body=rendered_s.body,
                status=EmailMessageStatus.DRAFT.value,
                subject_variant_index=rendered_s.subject_variant_index,
                body_variant_index=rendered_s.body_variant_index,
                offset_days=offset,
                earliest_send_at=None,
            )
            db.add(msg_s)

        # Transition lead status to in_sequence
        lead.status = LeadStatus.IN_SEQUENCE.value
        lead.updated_at = now
        db.add(
            Event(
                lead_id=lead.id,
                type="enrolled",
                payload={"campaign_id": target_campaign.id, "steps": 4},
                created_at=now,
            )
        )
        enrolled_leads.append(lead)

    db.commit()
    return enrolled_leads


def on_email_sent(
    email_message: EmailMessage,
    db: Session,
    clock: Clock | None = None,
) -> EmailMessage | None:
    """Advance the sequence lifecycle when an email is dispatched.

    Marks the next step as 'approved' and sets its earliest_send_at = sent_at + offset.
    If Step 4 was sent, the lead remains in sequence until evaluated for completion.
    """
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = email_message.lead or db.get(Lead, email_message.lead_id)
    if not lead:
        return None

    sent_time = email_message.sent_at or now
    if sent_time.tzinfo is None:
        sent_time = sent_time.replace(tzinfo=UTC)

    # Next step check
    next_step = email_message.step + 1
    next_msg = db.scalar(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.step == next_step,
        )
    )

    if next_msg is not None:
        offset = (
            next_msg.offset_days
            if next_msg.offset_days is not None
            else get_step_offset_days(db, lead.campaign_id, next_step)
        )
        next_msg.status = EmailMessageStatus.APPROVED.value
        next_msg.earliest_send_at = sent_time + timedelta(days=offset)
        db.commit()
        logger.info(
            "Promoted Step %d to approved for %s (earliest: %s)",
            next_step,
            lead.email,
            next_msg.earliest_send_at,
        )
        return next_msg

    db.commit()
    return None


def cancel_lead_sequence(
    lead_id: int,
    reason: str,
    db: Session,
    clock: Clock | None = None,
) -> int:
    """Cancel every remaining draft, approved, or scheduled email for a lead immediately.

    Executed within the current database transaction.
    """
    stmt = select(EmailMessage).where(
        EmailMessage.lead_id == lead_id,
        EmailMessage.status.in_(
            (
                EmailMessageStatus.DRAFT.value,
                EmailMessageStatus.APPROVED.value,
                EmailMessageStatus.SCHEDULED.value,
            )
        ),
    )
    pending_emails = db.scalars(stmt).all()
    count = 0
    for msg in pending_emails:
        msg.status = EmailMessageStatus.SKIPPED.value
        msg.error = f"Cancelled: {reason}"
        count += 1

    db.commit()
    logger.info("Cancelled %d pending messages for lead #%d (%s)", count, lead_id, reason)
    return count


def on_auto_reply(
    lead_id: int,
    db: Session,
    clock: Clock | None = None,
    delay_days: int = 5,
) -> list[EmailMessage]:
    """Handle out-of-office auto-reply: push earliest_send_at by 5 days without stopping."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    stmt = select(EmailMessage).where(
        EmailMessage.lead_id == lead_id,
        EmailMessage.status.in_(
            (
                EmailMessageStatus.APPROVED.value,
                EmailMessageStatus.SCHEDULED.value,
                EmailMessageStatus.DRAFT.value,
            )
        ),
    )
    pending_messages = db.scalars(stmt).all()

    for msg in pending_messages:
        base_time = msg.earliest_send_at or msg.scheduled_at or now
        if base_time.tzinfo is None:
            base_time = base_time.replace(tzinfo=UTC)
        msg.earliest_send_at = base_time + timedelta(days=delay_days)
        if msg.scheduled_at:
            msg.scheduled_at = msg.scheduled_at + timedelta(days=delay_days)

    db.add(
        Event(
            lead_id=lead_id,
            type=EventType.AUTO_REPLY.value,
            payload={"delay_days": delay_days, "action": "shifted_due_date"},
            created_at=now,
        )
    )
    db.commit()
    return pending_messages


def on_soft_bounce(
    email_message: EmailMessage,
    db: Session,
    clock: Clock | None = None,
) -> bool:
    """Handle soft bounce: retry the same step once after 24 hours."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = email_message.lead or db.get(Lead, email_message.lead_id)

    if email_message.retry_count < 1:
        email_message.retry_count += 1
        email_message.status = EmailMessageStatus.APPROVED.value
        email_message.earliest_send_at = now + timedelta(hours=24)
        email_message.scheduled_at = None

        if lead:
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.SOFT_BOUNCE.value,
                    payload={
                        "step": email_message.step,
                        "retry_count": email_message.retry_count,
                        "retry_at": email_message.earliest_send_at.isoformat(),
                    },
                    created_at=now,
                )
            )
        db.commit()
        return True

    # Already retried once for this message: mark as failed
    email_message.status = EmailMessageStatus.FAILED.value
    email_message.error = "Soft-bounce retry limit reached (1 retry)"
    db.commit()
    return False


def check_completed_leads(
    db: Session,
    clock: Clock | None = None,
) -> list[Lead]:
    """Identify leads whose final sequence step was sent > 7 days ago with no reply, and mark completed."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    stmt = select(Lead).where(Lead.status == LeadStatus.IN_SEQUENCE.value)
    leads_in_sequence = db.scalars(stmt).all()
    completed_leads: list[Lead] = []

    threshold_delta = timedelta(days=7)

    for lead in leads_in_sequence:
        max_step = db.scalar(
            select(func.max(SequenceStep.step)).where(
                SequenceStep.campaign_id == lead.campaign_id
            )
        ) or 4
        last_step_msg = db.scalar(
            select(EmailMessage).where(
                EmailMessage.lead_id == lead.id,
                EmailMessage.step == max_step,
            )
        )
        if (
            last_step_msg
            and last_step_msg.status
            in (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
            and last_step_msg.sent_at is not None
        ):
            sent_time = last_step_msg.sent_at
            if sent_time.tzinfo is None:
                sent_time = sent_time.replace(tzinfo=UTC)

            if now >= sent_time + threshold_delta:
                lead.status = LeadStatus.COMPLETED.value
                lead.updated_at = now
                db.add(
                    Event(
                        lead_id=lead.id,
                        type="completed",
                        payload={"step": max_step, "sent_at": sent_time.isoformat()},
                        created_at=now,
                    )
                )
                completed_leads.append(lead)

    db.commit()
    return completed_leads


def check_and_update_completed_campaigns(
    db: Session,
    clock: Clock | None = None,
    campaign_id: int | None = None,
) -> list[Campaign]:
    """Inspect active or paused campaigns and automatically mark them as COMPLETED

    only when all leads in the campaign have had ALL their follow-ups sent.

    A campaign is complete when:
    1. total_leads > 0 (it has at least 1 lead).
    2. Zero pending / scheduled / draft emails remain across the campaign:
       ['scheduled', 'draft', 'needs_review', 'approved', 'sending'].
    3. Every enrolled lead has had all follow-up steps sent (max sequence step has
       status 'sent' or 'sent_dry_run') or reached a terminal outcome
       ('replied', 'bounced', 'unsubscribed', 'won', 'invalid', 'completed').
    4. Zero leads remain in incomplete un-enrolled states:
       ['new', 'valid', 'personalized', 'approved', 'needs_manual_note', 'paused'].
    """
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    stmt = select(Campaign).where(
        Campaign.status.in_([CampaignStatus.ACTIVE.value, CampaignStatus.PAUSED.value])
    )
    if campaign_id is not None:
        stmt = stmt.where(Campaign.id == campaign_id)

    candidates = db.scalars(stmt).all()
    completed: list[Campaign] = []

    pending_email_statuses = [
        EmailMessageStatus.SCHEDULED.value,
        EmailMessageStatus.DRAFT.value,
        EmailMessageStatus.NEEDS_REVIEW.value,
        EmailMessageStatus.APPROVED.value,
        EmailMessageStatus.SENDING.value,
    ]

    for campaign in candidates:
        all_leads = db.scalars(
            select(Lead).where(Lead.campaign_id == campaign.id)
        ).all()
        if not all_leads:
            continue

        # 1. Any pending/draft/scheduled email means follow-ups are still pending!
        pending_emails_count = db.scalar(
            select(func.count(EmailMessage.id))
            .join(Lead, Lead.id == EmailMessage.lead_id)
            .where(
                Lead.campaign_id == campaign.id,
                EmailMessage.status.in_(pending_email_statuses),
            )
        ) or 0
        if pending_emails_count > 0:
            continue

        # 2. Get the maximum sequence step defined for this campaign
        max_step = db.scalar(
            select(func.max(SequenceStep.step)).where(
                SequenceStep.campaign_id == campaign.id
            )
        ) or 4

        # 3. Check every lead in the campaign
        all_leads_finished = True
        leads_to_mark_completed: list[Lead] = []

        for lead in all_leads:
            if lead.status in (
                LeadStatus.COMPLETED.value,
                LeadStatus.REPLIED.value,
                LeadStatus.BOUNCED.value,
                LeadStatus.UNSUBSCRIBED.value,
                LeadStatus.WON.value,
                LeadStatus.INVALID.value,
            ):
                continue

            if lead.status == LeadStatus.IN_SEQUENCE.value:
                # Verify that this lead's final follow-up step was actually sent
                final_msg = db.scalar(
                    select(EmailMessage).where(
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.step == max_step,
                    )
                )
                if (
                    final_msg
                    and final_msg.status in (
                        EmailMessageStatus.SENT.value,
                        EmailMessageStatus.SENT_DRY_RUN.value,
                    )
                ):
                    # All follow-up steps (1..max_step) have been sent!
                    leads_to_mark_completed.append(lead)
                    continue
                else:
                    # Final follow-up not sent yet!
                    all_leads_finished = False
                    break
            else:
                # Lead is in new, valid, personalized, approved, needs_manual_note, or paused
                all_leads_finished = False
                break

        if not all_leads_finished:
            continue

        # Mark finished leads as completed
        for lead in leads_to_mark_completed:
            lead.status = LeadStatus.COMPLETED.value
            lead.updated_at = now
            db.add(
                Event(
                    lead_id=lead.id,
                    type="completed",
                    payload={"step": max_step, "reason": "all_followups_sent"},
                    created_at=now,
                )
            )

        # Mark campaign as completed
        campaign.status = CampaignStatus.COMPLETED.value
        completed.append(campaign)
        logger.info(
            "Campaign #%d ('%s') automatically marked as COMPLETED: all %d leads and their follow-ups have been sent.",
            campaign.id,
            campaign.name,
            len(all_leads),
        )

    if completed:
        db.commit()

    return completed


# =========================================================================
# Manual operator actions
# =========================================================================


def pause_lead(lead_id: int, db: Session, clock: Clock | None = None) -> Lead:
    """Manually pause a lead in sequence."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    lead.status = LeadStatus.PAUSED.value
    lead.updated_at = now

    setting_key = f"LEAD_{lead_id}_PAUSED_AT"
    existing_setting = db.scalar(select(Setting).where(Setting.key == setting_key))
    if existing_setting:
        existing_setting.value = now.isoformat()
    else:
        db.add(Setting(key=setting_key, value=now.isoformat()))

    db.add(
        Event(
            lead_id=lead.id,
            type="lead_paused",
            payload={"manual": True},
            created_at=now,
        )
    )
    db.commit()
    return lead


def resume_lead(lead_id: int, db: Session, clock: Clock | None = None) -> Lead:
    """Manually resume a previously paused lead."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    setting_key = f"LEAD_{lead_id}_PAUSED_AT"
    pause_setting = db.scalar(select(Setting).where(Setting.key == setting_key))

    if pause_setting and pause_setting.value:
        try:
            paused_at = datetime.fromisoformat(pause_setting.value)
            if paused_at.tzinfo is None:
                paused_at = paused_at.replace(tzinfo=UTC)
            paused_duration = now - paused_at
            if paused_duration > timedelta(0):
                pending_msgs = db.scalars(
                    select(EmailMessage).where(
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.status.in_(
                            (
                                EmailMessageStatus.APPROVED.value,
                                EmailMessageStatus.SCHEDULED.value,
                                EmailMessageStatus.DRAFT.value,
                            )
                        ),
                    )
                ).all()
                for msg in pending_msgs:
                    if msg.earliest_send_at:
                        msg.earliest_send_at += paused_duration
                    if msg.scheduled_at:
                        msg.scheduled_at += paused_duration
        except Exception:
            pass
        db.delete(pause_setting)

    sent_count = db.scalar(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status.in_(
                (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
            ),
        )
    )
    lead.status = LeadStatus.IN_SEQUENCE.value if sent_count else LeadStatus.APPROVED.value
    lead.updated_at = now

    db.add(
        Event(
            lead_id=lead.id,
            type="lead_resumed",
            payload={"manual": True},
            created_at=now,
        )
    )
    db.commit()
    return lead


def mark_replied(lead_id: int, db: Session, clock: Clock | None = None) -> Lead:
    """Manually mark lead as replied and cancel all remaining emails."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    lead.status = LeadStatus.REPLIED.value
    lead.updated_at = now
    cancel_lead_sequence(lead_id, "manual mark_replied", db, clock)

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.REPLIED.value,
            payload={"manual": True},
            created_at=now,
        )
    )
    db.commit()
    return lead


def mark_won(lead_id: int, db: Session, clock: Clock | None = None) -> Lead:
    """Manually mark lead as won and cancel all remaining emails."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    lead.status = LeadStatus.WON.value
    lead.updated_at = now
    cancel_lead_sequence(lead_id, "manual mark_won", db, clock)

    db.add(
        Event(
            lead_id=lead.id,
            type="lead_won",
            payload={"manual": True},
            created_at=now,
        )
    )
    db.commit()
    return lead


def mark_not_interested(lead_id: int, db: Session, clock: Clock | None = None) -> Lead:
    """Manually mark lead not interested, suppress them, and cancel remaining emails."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    lead = db.get(Lead, lead_id)
    if not lead:
        raise ValueError(f"Lead #{lead_id} not found")

    lead.status = LeadStatus.UNSUBSCRIBED.value
    lead.updated_at = now
    add_suppression(db, target=lead.email, reason="not_interested", clock=clk)
    cancel_lead_sequence(lead_id, "manual not_interested", db, clock)

    db.add(
        Event(
            lead_id=lead.id,
            type=EventType.UNSUBSCRIBED.value,
            payload={"manual": True, "reason": "not_interested"},
            created_at=now,
        )
    )
    db.commit()
    return lead


def skip_step(lead_id: int, step: int, db: Session, clock: Clock | None = None) -> EmailMessage:
    """Manually skip a specific sequence step and advance to the next step."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    msg = db.scalar(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead_id,
            EmailMessage.step == step,
        )
    )
    if not msg:
        raise ValueError(f"Email message for lead #{lead_id} step {step} not found")

    msg.status = EmailMessageStatus.SKIPPED.value
    msg.error = "Manually skipped step"

    # Promote next step if exists
    next_msg = db.scalar(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead_id,
            EmailMessage.step == step + 1,
        )
    )
    if next_msg:
        next_msg.status = EmailMessageStatus.APPROVED.value
        next_msg.earliest_send_at = now

    db.add(
        Event(
            lead_id=lead_id,
            type="step_skipped",
            payload={"step": step, "manual": True},
            created_at=now,
        )
    )
    db.commit()
    return msg


def pause_campaign(campaign_id: int, db: Session, clock: Clock | None = None) -> Campaign:
    """Pause an active campaign and snapshot the pause timestamp."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign #{campaign_id} not found")

    campaign.status = CampaignStatus.PAUSED.value

    setting_key = f"CAMPAIGN_{campaign_id}_PAUSED_AT"
    existing = db.scalar(select(Setting).where(Setting.key == setting_key))
    if existing:
        existing.value = now.isoformat()
    else:
        db.add(Setting(key=setting_key, value=now.isoformat()))

    db.add(
        Event(
            lead_id=None,
            type="campaign_paused",
            payload={"campaign_id": campaign_id},
            created_at=now,
        )
    )
    db.commit()
    logger.info("Paused campaign #%d (%s)", campaign_id, campaign.name)
    return campaign


def resume_campaign(campaign_id: int, db: Session, clock: Clock | None = None) -> Campaign:
    """Resume a paused campaign and shift pending email due dates by paused duration."""
    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    campaign = db.get(Campaign, campaign_id)
    if not campaign:
        raise ValueError(f"Campaign #{campaign_id} not found")

    campaign.status = CampaignStatus.ACTIVE.value

    setting_key = f"CAMPAIGN_{campaign_id}_PAUSED_AT"
    paused_setting = db.scalar(select(Setting).where(Setting.key == setting_key))

    shifted_seconds = 0.0
    if paused_setting and paused_setting.value:
        try:
            paused_at = datetime.fromisoformat(paused_setting.value)
            if paused_at.tzinfo is None:
                paused_at = paused_at.replace(tzinfo=UTC)
            paused_duration = now - paused_at
            if paused_duration > timedelta(0):
                shifted_seconds = paused_duration.total_seconds()
                # Shift earliest_send_at and scheduled_at on all pending messages for this campaign
                pending_msgs = db.scalars(
                    select(EmailMessage)
                    .join(Lead)
                    .where(
                        Lead.campaign_id == campaign_id,
                        EmailMessage.status.in_(
                            (
                                EmailMessageStatus.APPROVED.value,
                                EmailMessageStatus.SCHEDULED.value,
                                EmailMessageStatus.DRAFT.value,
                            )
                        ),
                    )
                ).all()
                for msg in pending_msgs:
                    if msg.earliest_send_at:
                        msg.earliest_send_at += paused_duration
                    if msg.scheduled_at:
                        msg.scheduled_at += paused_duration
        except Exception as err:
            logger.error("Error shifting campaign due dates on resume: %s", err)
        db.delete(paused_setting)

    db.add(
        Event(
            lead_id=None,
            type="campaign_resumed",
            payload={
                "campaign_id": campaign_id,
                "shifted_duration_seconds": shifted_seconds,
            },
            created_at=now,
        )
    )
    db.commit()
    logger.info(
        "Resumed campaign #%d (%s), shifted %s s", campaign_id, campaign.name, shifted_seconds
    )
    return campaign
