"""Background scheduler and dispatch worker loop for ColdReach.

Implements safe outbound execution:
1. Poll every 30s: find EmailMessages with status scheduled and scheduled_at <= now.
2. Global kill-switch and paused campaign evaluation.
3. Pre-send verification: lead eligibility, linter check, daily cap, minimum gap.
4. Concurrency safety: atomic row claiming (status -> sending).
5. Safe dispatch through configured MailSender.
6. Robust error classification:
   - Transient errors: retry with 10m and 30m backoff (up to 2 retries).
   - Permanent errors: mark email failed and lead bounced.
   - Auth errors / account suspension signals: trigger global kill-switch and halt loop.
7. Crash safety: recover emails stuck in 'sending' (>10m) on startup into 'needs_review'.
"""

import signal
import sys
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import Clock, system_clock
from app.core.config import get_settings
from app.core.db import SessionLocal, init_db
from app.core.logging import get_logger, setup_logging
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.services.inbox import (
    FakeMailboxReader,
    ImapReader,
    MailboxReader,
    poll_inbox_and_process,
)
from app.services.linter import lint_email
from app.services.notifier import LogNotifier, Notifier, send_daily_summary
from app.services.planner import (
    HARD_MAX_LIMIT,
    get_mailbox_tz,
    get_outreach_start_date,
    get_ramp_up_cap,
    get_setting,
)
from app.services.safety import (
    evaluate_and_enforce_safety,
    is_cap_halved_from_resume,
    is_killswitch_active,
    trigger_killswitch,
)
from app.services.sender import (
    KILLSWITCH_KEYWORDS,
    MailSender,
    OutgoingMail,
    get_sender,
)
from app.services.sequencer import (
    can_send,
    check_completed_leads,
    on_email_sent,
)
from app.services.suppression import is_suppressed

logger = get_logger(__name__)


def recover_stuck_sending_messages(db: Session, clock: Clock) -> int:
    """Recover emails stuck in 'sending' across crashes or restarts (>10m).

    Mark them 'needs_review' and log an audit Event so they are NEVER resent automatically.
    """
    now = clock.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    threshold = now - timedelta(minutes=10)

    stmt = select(EmailMessage).where(EmailMessage.status == EmailMessageStatus.SENDING.value)
    stuck_messages = db.scalars(stmt).all()
    count = 0

    for msg in stuck_messages:
        started_at = msg.sending_started_at or msg.scheduled_at
        if started_at is not None and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)

        if started_at is None or started_at <= threshold:
            msg.status = EmailMessageStatus.NEEDS_REVIEW.value
            msg.error = "Stuck in 'sending' across restart (>10m); marked for manual review"
            db.add(
                Event(
                    lead_id=msg.lead_id,
                    type="needs_review",
                    payload={
                        "email_message_id": msg.id,
                        "reason": "stuck_sending_recovery",
                        "started_at": started_at.isoformat() if started_at else None,
                    },
                    created_at=now,
                )
            )
            count += 1

    if count > 0:
        db.commit()
        logger.warning(
            "Crash safety: recovered %d stuck 'sending' message(s) to 'needs_review'", count
        )
    return count


def get_daily_send_cap(db: Session, target_date: date) -> int:
    """Calculate maximum allowable dispatches for target_date.

    Clamped unconditionally by HARD_MAX_LIMIT (30).
    """
    start_date = get_outreach_start_date(db)
    if target_date < start_date:
        return 0

    configured_max = get_setting(db, "HARD_MAX_PER_DAY", default=HARD_MAX_LIMIT)
    try:
        configured_max_int = int(configured_max)
    except Exception:
        configured_max_int = HARD_MAX_LIMIT
    hard_limit = min(configured_max_int, HARD_MAX_LIMIT)

    # Optional test/operator bypass flag for dry-run capacity checks
    ignore_ramp_up = str(get_setting(db, "IGNORE_RAMP_UP", default="false")).lower() in (
        "true",
        "1",
        "yes",
    )
    if ignore_ramp_up:
        return hard_limit

    base_cap = get_ramp_up_cap(target_date, start_date)
    final_cap = min(base_cap, hard_limit)

    if is_cap_halved_from_resume(db, target_date):
        final_cap = max(1, final_cap // 2)

    return final_cap


def get_sent_today_count(db: Session, today_date: date, mailbox_tz: ZoneInfo) -> int:
    """Count how many emails have already been dispatched today across the mailbox."""
    stmt = select(EmailMessage).where(
        EmailMessage.status.in_(
            (
                EmailMessageStatus.SENT.value,
                EmailMessageStatus.SENT_DRY_RUN.value,
            )
        )
    )
    sent_messages = db.scalars(stmt).all()
    count = 0
    for m in sent_messages:
        ref_dt = m.sent_at or m.scheduled_at
        if ref_dt is not None:
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=UTC)
            if ref_dt.astimezone(mailbox_tz).date() == today_date:
                count += 1
    return count


class Worker:
    """Outbound background dispatch worker loop."""

    def __init__(
        self,
        poll_interval: float = 30.0,
        sender: MailSender | None = None,
        clock: Clock | None = None,
        session_factory: Callable[[], Session] | sessionmaker | None = None,
        inbox_reader: MailboxReader | None = None,
        inbox_poll_interval: float | None = None,
        notifier: Notifier | None = None,
    ) -> None:
        self.poll_interval = poll_interval
        self.clock = clock or system_clock
        self._sender = sender
        self.inbox_reader = inbox_reader
        self.session_factory = session_factory or SessionLocal
        self.settings = get_settings()
        self.inbox_poll_interval = inbox_poll_interval or float(
            self.settings.INBOX_POLL_INTERVAL_SECONDS
        )
        self.notifier = notifier or LogNotifier()
        self._last_inbox_poll: datetime | None = None
        self._running = True

    def get_sender(self, db: Session) -> MailSender:
        """Return configured or injected MailSender."""
        if self._sender is not None:
            return self._sender
        return get_sender(db=db)

    def get_inbox_reader(self) -> MailboxReader:
        """Return configured or injected MailboxReader."""
        if self.inbox_reader is not None:
            return self.inbox_reader
        if self.settings.LIVE_MODE:
            return ImapReader(settings=self.settings)
        return FakeMailboxReader()

    def poll_inbox(self, db: Session, now: datetime) -> tuple[int, dict[str, float]]:
        """Poll incoming emails, process bounces/replies, and calculate bounce rates."""
        try:
            reader = self.get_inbox_reader()
            new_count, stats = poll_inbox_and_process(
                reader=reader, db=db, clock=self.clock, notifier=self.notifier
            )
            self._last_inbox_poll = now
            # Recalculate health metrics and enforce safety rules after every inbox poll
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return new_count, stats
        except Exception as e:
            logger.warning("Inbox polling transient failure: %s", e)
            self._last_inbox_poll = now
            return 0, {}

    def stop(self, *_) -> None:
        """Signal handler to terminate worker loop gracefully."""
        logger.info("Termination signal received. Stopping worker loop...")
        self._running = False

    def process_message(
        self,
        msg_id: int,
        db: Session,
        sender: MailSender,
        now: datetime,
    ) -> bool:
        """Evaluate pre-send rules, claim row atomically, and dispatch an email.

        Returns:
            True if message was sent or handled successfully, False if worker loop should halt.
        """
        # Re-fetch candidate message
        msg = db.get(EmailMessage, msg_id)
        if not msg or msg.status != EmailMessageStatus.SCHEDULED.value:
            return True

        lead = msg.lead or db.get(Lead, msg.lead_id)
        if not lead:
            msg.status = EmailMessageStatus.SKIPPED.value
            msg.error = "Lead record missing"
            db.commit()
            return True

        # 1. Global Killswitch Check
        if is_killswitch_active(db):
            logger.warning("Kill-switch is active. Refusing dispatch for message #%d.", msg.id)
            return False

        # 2. Central gatekeeper verification via can_send
        if not can_send(lead, msg, db, clock=self.clock):
            terminal_statuses = (
                LeadStatus.REPLIED.value,
                LeadStatus.BOUNCED.value,
                LeadStatus.UNSUBSCRIBED.value,
                LeadStatus.INVALID.value,
                LeadStatus.COMPLETED.value,
            )
            if lead.status in terminal_statuses:
                msg.status = EmailMessageStatus.SKIPPED.value
                msg.error = f"Lead in terminal status: {lead.status}"
            elif is_suppressed(db, lead.email):
                msg.status = EmailMessageStatus.SKIPPED.value
                msg.error = "Lead is on suppression list"
                lead.status = LeadStatus.UNSUBSCRIBED.value
            db.commit()
            logger.info("can_send rejected message #%d for %s", msg.id, lead.email)
            return True

        campaign = lead.campaign or db.get(Campaign, lead.campaign_id)

        # 3. Campaign Paused Check
        if campaign and campaign.status != CampaignStatus.ACTIVE.value:
            logger.info("Campaign %s is not active; holding message #%d", campaign.name, msg.id)
            return True

        if not campaign or campaign.status == CampaignStatus.PAUSED.value:
            c_name = campaign.name if campaign else "N/A"
            logger.info("Campaign %s is paused. Skipping message #%d.", c_name, msg.id)
            return True

        # 2. Pre-send Checks (re-evaluated on every send attempt)
        # 2a. Lead eligibility
        terminal_statuses = (
            LeadStatus.REPLIED.value,
            LeadStatus.BOUNCED.value,
            LeadStatus.UNSUBSCRIBED.value,
            LeadStatus.INVALID.value,
        )
        if lead.status in terminal_statuses:
            msg.status = EmailMessageStatus.SKIPPED.value
            msg.error = f"Lead in terminal status: {lead.status}"
            db.commit()
            logger.info("Skipped email #%d: lead %s is %s", msg.id, lead.email, lead.status)
            return True

        # 2b. Global suppression re-check
        if is_suppressed(db, lead.email):
            msg.status = EmailMessageStatus.SKIPPED.value
            msg.error = "Lead is on suppression list"
            lead.status = LeadStatus.UNSUBSCRIBED.value
            db.commit()
            logger.info("Skipped email #%d: %s is suppressed", msg.id, lead.email)
            return True

        # 2c. Spam linter verification
        lint_res = lint_email(subject=msg.subject, body=msg.body, step=msg.step)
        if not lint_res.passed:
            msg.status = EmailMessageStatus.SKIPPED.value
            msg.error = f"Linter check failed: {'; '.join(lint_res.failures)}"
            db.commit()
            logger.warning("Skipped email #%d: linter failed (%s)", msg.id, msg.error)
            return True

        # 2d. Today's capacity check against database (not memory)
        mailbox_tz = get_mailbox_tz(db)
        today_date = now.astimezone(mailbox_tz).date()
        today_cap = get_daily_send_cap(db, today_date)
        sent_today = get_sent_today_count(db, today_date, mailbox_tz)
        if sent_today >= today_cap:
            logger.info(
                "Today's sending cap reached (%d/%d) for date %s; holding message #%d",
                sent_today,
                today_cap,
                today_date,
                msg.id,
            )
            return True

        # 2e. Minimum gap since last send across mailbox
        stmt_last_sent = select(func.max(EmailMessage.sent_at)).where(
            EmailMessage.status.in_(
                (
                    EmailMessageStatus.SENT.value,
                    EmailMessageStatus.SENT_DRY_RUN.value,
                )
            )
        )
        last_sent_at = db.scalar(stmt_last_sent)
        if last_sent_at is not None:
            if last_sent_at.tzinfo is None:
                last_sent_at = last_sent_at.replace(tzinfo=UTC)
            min_gap_sec = int(get_setting(db, "MIN_GAP_SECONDS", default=120))
            elapsed = (now - last_sent_at).total_seconds()
            if elapsed < min_gap_sec:
                logger.info(
                    "Minimum dispatch gap not reached (elapsed %ds < min %ds); holding message #%d",
                    int(elapsed),
                    min_gap_sec,
                    msg.id,
                )
                return True

        # 3. Atomically claim row (status -> sending)
        claim_stmt = (
            update(EmailMessage)
            .where(
                EmailMessage.id == msg.id,
                EmailMessage.status == EmailMessageStatus.SCHEDULED.value,
            )
            .values(
                status=EmailMessageStatus.SENDING.value,
                sending_started_at=now,
            )
        )
        claim_res = db.execute(claim_stmt)
        db.commit()

        if claim_res.rowcount == 0:
            # Concurrently claimed by another worker process
            logger.debug("Message #%d claimed by another worker; skipping", msg.id)
            return True

        # Re-fetch locked row
        db.refresh(msg)

        # 4. Build outgoing payload
        step1_msg_id: str | None = None
        if msg.step > 1:
            step1 = db.scalar(
                select(EmailMessage).where(
                    EmailMessage.lead_id == lead.id,
                    EmailMessage.step == 1,
                )
            )
            if step1 and step1.message_id:
                step1_msg_id = step1.message_id

        lead_name = f"{lead.first_name or ''} {lead.last_name or ''}".strip() or None
        outgoing = OutgoingMail(
            to_email=lead.email,
            to_name=lead_name,
            from_email=self.settings.SMTP_FROM_EMAIL,
            from_name=self.settings.SMTP_FROM_NAME,
            reply_to=self.settings.SMTP_FROM_EMAIL,
            subject=msg.subject,
            body=msg.body,
            in_reply_to=step1_msg_id,
            references=step1_msg_id,
            lead_id=lead.id,
            email_message_id=msg.id,
            step=msg.step,
        )

        # 5. Dispatch through sender
        result = sender.send(outgoing)

        # 6. Process SendResult
        if result.success:
            final_status = (
                EmailMessageStatus.SENT_DRY_RUN.value
                if result.dry_run
                else EmailMessageStatus.SENT.value
            )
            msg.status = final_status
            msg.sent_at = now
            msg.message_id = result.message_id
            lead.status = LeadStatus.IN_SEQUENCE.value

            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.SENT.value,
                    payload={
                        "step": msg.step,
                        "message_id": result.message_id,
                        "dry_run": result.dry_run,
                    },
                    created_at=now,
                )
            )
            db.commit()
            logger.info("Successfully dispatched message #%d to %s", msg.id, lead.email)

            # Advance sequencer lifecycle for next sequence step
            on_email_sent(msg, db, clock=self.clock)

            # Recalculate health metrics and enforce safety rules after every send
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return True

        # Dispatch Failure Handling
        err_msg = result.error or "Unknown delivery error"
        lower_err = err_msg.lower()

        # Check Killswitch triggers: auth failure or blocked/suspended keywords
        is_killswitch = (
            result.error_type in ("auth", "killswitch")
            or "auth" in lower_err
            or any(kw in lower_err for kw in KILLSWITCH_KEYWORDS)
        )
        if is_killswitch:
            trigger_killswitch(
                db,
                reason=err_msg,
                notifier=self.notifier,
                clock=self.clock,
            )
            msg.status = EmailMessageStatus.FAILED.value
            msg.error = f"Kill-switch triggered: {err_msg}"
            db.commit()
            return False  # Halt worker loop immediately

        # Permanent failure (5xx recipient rejected)
        if result.error_type == "permanent":
            msg.status = EmailMessageStatus.FAILED.value
            msg.error = f"Permanent send failure: {err_msg}"
            lead.status = LeadStatus.BOUNCED.value
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.BOUNCED.value,
                    payload={"step": msg.step, "error": err_msg},
                    created_at=now,
                )
            )
            db.commit()
            logger.error("Permanent send error for lead %s: %s", lead.email, err_msg)
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return True

        # Transient failure (SMTP 4xx, network timeout) -> retry up to 2 times
        if msg.retry_count == 0:
            msg.retry_count = 1
            msg.status = EmailMessageStatus.SCHEDULED.value
            msg.scheduled_at = now + timedelta(minutes=10)
            msg.error = f"Transient error (retry 1/2 in 10m): {err_msg}"
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.ERROR.value,
                    payload={"retry": 1, "error": err_msg, "backoff_minutes": 10},
                    created_at=now,
                )
            )
            db.commit()
            logger.warning("Message #%d scheduled for retry 1 in 10m: %s", msg.id, err_msg)
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return True
        elif msg.retry_count == 1:
            msg.retry_count = 2
            msg.status = EmailMessageStatus.SCHEDULED.value
            msg.scheduled_at = now + timedelta(minutes=30)
            msg.error = f"Transient error (retry 2/2 in 30m): {err_msg}"
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.ERROR.value,
                    payload={"retry": 2, "error": err_msg, "backoff_minutes": 30},
                    created_at=now,
                )
            )
            db.commit()
            logger.warning("Message #%d scheduled for retry 2 in 30m: %s", msg.id, err_msg)
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return True
        else:
            # Retried twice already; mark failed permanently
            msg.status = EmailMessageStatus.FAILED.value
            msg.error = f"Failed after 2 retries: {err_msg}"
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.ERROR.value,
                    payload={"final": True, "error": err_msg, "retries": 2},
                    created_at=now,
                )
            )
            db.commit()
            logger.error("Message #%d permanently failed after 2 retries", msg.id)
            evaluate_and_enforce_safety(db, notifier=self.notifier, clock=self.clock)
            return True

    def run_once(self, poll_inbox_now: bool | None = None) -> int:
        """Execute a single polling and dispatch cycle.

        Args:
            poll_inbox_now: Optional manual override to force inbox polling during this cycle.

        Returns:
            Number of successfully dispatched emails.
        """
        now = self.clock.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        with self.session_factory() as db:
            # 1. Periodic inbox monitoring poll (default every 3m / configurable)
            should_poll = False
            if poll_inbox_now is True:
                should_poll = True
            elif poll_inbox_now is None:
                if self._last_inbox_poll is None:
                    should_poll = True
                else:
                    elapsed = (now - self._last_inbox_poll).total_seconds()
                    if elapsed >= self.inbox_poll_interval:
                        should_poll = True

            if should_poll:
                self.poll_inbox(db, now)

            # Daily summary dispatch (configurable hour, once per calendar day)
            send_daily_summary(db, notifier=self.notifier, clock=self.clock)

            # 2. Check for sequence completion (Step 4 sent > 7 days ago with no reply)
            check_completed_leads(db, clock=self.clock)

            # 3. Check global killswitch
            if is_killswitch_active(db):
                logger.warning("Global kill-switch active. Worker cycle skipped.")
                return 0

            # Query scheduled messages due for dispatch
            stmt = (
                select(EmailMessage.id)
                .where(
                    EmailMessage.status == EmailMessageStatus.SCHEDULED.value,
                    EmailMessage.scheduled_at <= now,
                )
                .order_by(EmailMessage.scheduled_at.asc())
            )
            candidate_ids = db.scalars(stmt).all()
            if not candidate_ids:
                return 0

            sender = self.get_sender(db=db)
            sent_count = 0

            for msg_id in candidate_ids:
                if not self._running:
                    break

                continue_loop = self.process_message(msg_id, db, sender, now)
                if not continue_loop:
                    self._running = False
                    break

                # Check if message status transitioned to sent
                refreshed = db.get(EmailMessage, msg_id)
                if refreshed and refreshed.status in (
                    EmailMessageStatus.SENT.value,
                    EmailMessageStatus.SENT_DRY_RUN.value,
                ):
                    sent_count += 1

            return sent_count

    def start(self) -> None:
        """Start the worker execution loop with signal handling and crash recovery."""
        setup_logging()
        init_db()

        logger.info(
            "ColdReach Worker starting. SAFE BY DEFAULT: LIVE_MODE=%s (dry-run=%s)",
            self.settings.LIVE_MODE,
            not self.settings.LIVE_MODE,
        )

        # 1. Startup crash safety: recover stuck 'sending' messages (>10m)
        with self.session_factory() as db:
            recover_stuck_sending_messages(db, self.clock)

        signal.signal(signal.SIGINT, self.stop)
        signal.signal(signal.SIGTERM, self.stop)

        try:
            while self._running:
                sent = self.run_once()
                if sent > 0:
                    logger.info("Cycle completed: %d email(s) dispatched", sent)

                # Sleep interval
                for _ in range(int(self.poll_interval)):
                    if not self._running:
                        break
                    time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Worker interrupted by user.")
        finally:
            logger.info("Worker process terminated.")


if __name__ == "__main__":
    worker = Worker()
    worker.start()
    sys.exit(0)
