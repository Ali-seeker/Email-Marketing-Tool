"""Inbound mailbox monitoring service.

Implements SAFE BY DEFAULT read-only architecture:
- MailboxReader abstract base interface.
- FakeMailboxReader: loads .eml fixture files for deterministic testing.
- ImapReader: read-only IMAP reader over SSL using BODY.PEEK (LIVE_MODE=true only).
"""

from __future__ import annotations

import imaplib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage as PyEmailMessage
from email.policy import default
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

if TYPE_CHECKING:
    from app.services.classifier import ClassificationResult
    from app.services.notifier import Notifier

from app.core.clock import Clock, system_clock
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.suppression import add as add_suppression

logger = get_logger(__name__)

# Pattern to extract individual message IDs from References / In-Reply-To
MSG_ID_PATTERN = re.compile(r"<[^>]+>")


@dataclass
class IncomingMail:
    """Normalized structured representation of an incoming inbox email."""

    uid: int
    raw_bytes: bytes
    message_id: str | None
    from_email: str
    from_name: str | None
    to_email: str
    subject: str
    date: datetime | None
    in_reply_to: str | None
    references: list[str] = field(default_factory=list)
    body_text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = "text/plain"
    is_multipart_report: bool = False
    raw_message: PyEmailMessage | None = None

    @classmethod
    def from_bytes(cls, raw_bytes: bytes, uid: int = 1) -> IncomingMail:
        """Parse raw RFC822 email bytes into IncomingMail."""
        msg: PyEmailMessage = message_from_bytes(raw_bytes, policy=default)

        # Parse From and To
        from_raw = str(msg.get("From", ""))
        from_name_val, from_email_val = parseaddr(from_raw)
        from_email_clean = from_email_val.strip().lower()

        to_raw = str(msg.get("To", ""))
        _, to_email_val = parseaddr(to_raw)
        to_email_clean = to_email_val.strip().lower()

        # Subject & Message-ID
        subject = str(msg.get("Subject", "")).strip()
        msg_id_raw = str(msg.get("Message-ID", "")).strip() or None

        # Threading: In-Reply-To and References
        in_reply_to_raw = str(msg.get("In-Reply-To", "")).strip()
        in_reply_to = in_reply_to_raw if in_reply_to_raw else None

        references_raw = str(msg.get("References", "")).strip()
        references = MSG_ID_PATTERN.findall(references_raw) if references_raw else []
        if in_reply_to and in_reply_to not in references:
            references.append(in_reply_to)

        # Date header
        date_header = msg.get("Date")
        parsed_dt: datetime | None = None
        if date_header:
            try:
                parsed_dt = parsedate_to_datetime(str(date_header))
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=UTC)
            except Exception:
                parsed_dt = None

        # Extract text body across mime parts
        content_type = msg.get_content_type()
        is_multipart_report = content_type == "multipart/report"

        body_text_parts: list[str] = []
        if msg.is_multipart():
            for part in msg.walk():
                part_type = part.get_content_type()
                if part_type in ("text/plain", "message/delivery-status", "text/rfc822-headers"):
                    try:
                        content = part.get_content()
                        if isinstance(content, str):
                            body_text_parts.append(content)
                        elif isinstance(content, bytes):
                            body_text_parts.append(content.decode("utf-8", errors="replace"))
                    except Exception:
                        payload = part.get_payload(decode=True)
                        if isinstance(payload, bytes):
                            body_text_parts.append(payload.decode("utf-8", errors="replace"))
                elif part_type == "text/html" and not body_text_parts:
                    # Fallback to plain HTML if no text/plain part exists
                    try:
                        html_content = part.get_content()
                        if isinstance(html_content, str):
                            # Basic strip of html tags
                            clean_text = re.sub(r"<[^>]+>", " ", html_content)
                            body_text_parts.append(clean_text)
                    except Exception:
                        pass
        else:
            try:
                content = msg.get_content()
                if isinstance(content, str):
                    body_text_parts.append(content)
                elif isinstance(content, bytes):
                    body_text_parts.append(content.decode("utf-8", errors="replace"))
            except Exception:
                payload = msg.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body_text_parts.append(payload.decode("utf-8", errors="replace"))

        body_text = "\n\n".join(body_text_parts).strip()

        # Capture headers
        headers_dict = {k: str(v) for k, v in msg.items()}

        return cls(
            uid=uid,
            raw_bytes=raw_bytes,
            message_id=msg_id_raw,
            from_email=from_email_clean,
            from_name=from_name_val if from_name_val else None,
            to_email=to_email_clean,
            subject=subject,
            date=parsed_dt,
            in_reply_to=in_reply_to,
            references=references,
            body_text=body_text,
            headers=headers_dict,
            content_type=content_type,
            is_multipart_report=is_multipart_report,
            raw_message=msg,
        )


class MailboxReader(ABC):
    """Abstract contract for reading new inbound emails from an inbox."""

    @abstractmethod
    def fetch_new(self, since_uid: int = 0) -> list[IncomingMail]:
        """Fetch incoming messages with UID strictly greater than since_uid.

        Args:
            since_uid: Last checkpointed UID.

        Returns:
            List of newly arrived IncomingMail instances sorted by UID.
        """
        raise NotImplementedError


class FakeMailboxReader(MailboxReader):
    """Deterministic in-memory reader that loads .eml fixture files for tests."""

    def __init__(
        self,
        fixtures_dir: Path | str | None = None,
        messages: list[IncomingMail] | None = None,
    ) -> None:
        self.messages: list[IncomingMail] = list(messages or [])
        self.command_log: list[str] = []

        if fixtures_dir:
            f_path = Path(fixtures_dir)
            if f_path.is_dir():
                eml_files = sorted(f_path.glob("*.eml"))
                for idx, eml_file in enumerate(eml_files, start=1):
                    mail = IncomingMail.from_bytes(eml_file.read_bytes(), uid=idx)
                    self.messages.append(mail)

    def add_message(self, raw_bytes_or_mail: bytes | IncomingMail) -> IncomingMail:
        """Add a raw email or IncomingMail to the fake mailbox assigning next sequential UID."""
        if isinstance(raw_bytes_or_mail, IncomingMail):
            next_uid = max([m.uid for m in self.messages], default=0) + 1
            if not raw_bytes_or_mail.uid or raw_bytes_or_mail.uid < next_uid:
                raw_bytes_or_mail.uid = next_uid
            self.messages.append(raw_bytes_or_mail)
            return raw_bytes_or_mail

        next_uid = max([m.uid for m in self.messages], default=0) + 1
        mail = IncomingMail.from_bytes(raw_bytes_or_mail, uid=next_uid)
        self.messages.append(mail)
        return mail

    def fetch_new(self, since_uid: int = 0) -> list[IncomingMail]:
        """Fetch emails with UID > since_uid."""
        self.command_log.append(f"FETCH_NEW since_uid={since_uid}")
        new_msgs = [m for m in self.messages if m.uid > since_uid]
        new_msgs.sort(key=lambda m: m.uid)
        return new_msgs


class ImapReader(MailboxReader):
    """Read-only IMAP reader over SSL used when LIVE_MODE=true.

    Strictly guarantees read-only access:
    - Opens folder with SELECT readonly=True
    - Reads messages using BODY.PEEK[] so messages remain unread
    - Never issues STORE, EXPUNGE, or DELETE commands.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        imap_client_factory: Any | None = None,
        mailbox: Any | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.client_factory = imap_client_factory or imaplib.IMAP4_SSL
        self.mailbox = mailbox

    def fetch_new(self, since_uid: int = 0) -> list[IncomingMail]:
        """Fetch newly arrived messages via IMAP without mutating mailbox state."""
        if not self.settings.LIVE_MODE:
            logger.warning(
                "ImapReader called while LIVE_MODE=false. "
                "Zero network sockets permitted by RULES.md."
            )
            return []

        if self.mailbox:
            host = self.mailbox.imap_host
            port = self.mailbox.imap_port
            user = self.mailbox.imap_username or self.mailbox.email
            password = self.mailbox.imap_password
        else:
            host = self.settings.IMAP_HOST
            port = self.settings.IMAP_PORT
            user = self.settings.imap_username
            password = self.settings.imap_password

        if not user or not password:
            logger.error("IMAP credentials missing; skipping inbox fetch.")
            return []

        client: imaplib.IMAP4_SSL | Any = self.client_factory(host, port)
        results: list[IncomingMail] = []

        try:
            client.login(user, password)
            # Must be selected readonly!
            client.select("INBOX", readonly=True)

            search_query = f"UID {since_uid + 1}:*" if since_uid > 0 else "ALL"
            typ, data = client.uid("SEARCH", search_query)
            if typ != "OK" or not data or not data[0]:
                return []

            uid_strs = data[0].split()
            # If since_uid was 0 (first time polling this mailbox), limit to the most recent 50 emails
            # so we don't fetch an entire years-old mailbox history over network.
            if since_uid == 0 and len(uid_strs) > 50:
                uid_strs = uid_strs[-50:]

            for uid_bytes in uid_strs:
                try:
                    uid_int = int(uid_bytes)
                except ValueError:
                    continue

                if uid_int <= since_uid:
                    continue

                # Fetch RFC822 bytes with BODY.PEEK[] to preserve unread flag
                fetch_typ, fetch_data = client.uid("FETCH", str(uid_int), "(BODY.PEEK[])")
                if fetch_typ != "OK" or not fetch_data:
                    continue

                raw_bytes: bytes | None = None
                for response_part in fetch_data:
                    if isinstance(response_part, tuple) and len(response_part) > 1:
                        raw_bytes = response_part[1]
                        break

                if raw_bytes:
                    incoming = IncomingMail.from_bytes(raw_bytes, uid=uid_int)
                    results.append(incoming)

            results.sort(key=lambda m: m.uid)
            return results

        except Exception as e:
            logger.warning("IMAP fetch failed (%s): %s", host, e)
            return []

        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                client.logout()
            except Exception:
                pass


def process_incoming_mail(
    mail: IncomingMail,
    db: Session,
    clock: Clock | None = None,
) -> ClassificationResult:
    """Classify incoming email and execute lifecycle state mutations."""
    from app.models.email_message import EmailMessage, EmailMessageStatus
    from app.models.event import Event, EventType
    from app.models.lead import LeadStatus
    from app.models.reply import Reply
    from app.services.classifier import InboundClassification, classify_inbound_mail

    clk = clock or system_clock
    now = clk.now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    result = classify_inbound_mail(mail, db)
    lead = result.matched_lead

    if result.classification == InboundClassification.UNRELATED:
        logger.debug("Ignored unrelated incoming email: %s", mail.subject)
        return result

    if result.classification == InboundClassification.HARD_BOUNCE:
        if lead:
            lead.status = LeadStatus.BOUNCED.value
            add_suppression(db, target=lead.email, reason="bounce")
            db.execute(
                update(EmailMessage)
                .where(
                    EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(
                        (
                            EmailMessageStatus.DRAFT.value,
                            EmailMessageStatus.APPROVED.value,
                            EmailMessageStatus.SCHEDULED.value,
                        )
                    ),
                )
                .values(
                    status=EmailMessageStatus.SKIPPED.value,
                    error="Cancelled due to hard bounce",
                )
            )
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.BOUNCED.value,
                    payload={
                        "subject": mail.subject,
                        "recipient": result.bounce_recipient or lead.email,
                        "status": "hard_bounce",
                    },
                    created_at=now,
                )
            )
            db.commit()
            logger.info("Hard bounce processed for lead %s", lead.email)

    elif result.classification == InboundClassification.SOFT_BOUNCE:
        if lead:
            stmt = select(func.count(Event.id)).where(
                Event.lead_id == lead.id,
                Event.type == EventType.SOFT_BOUNCE.value,
            )
            prior_count = db.scalar(stmt) or 0

            if prior_count >= 2:
                lead.status = LeadStatus.BOUNCED.value
                add_suppression(db, target=lead.email, reason="bounce")
                db.execute(
                    update(EmailMessage)
                    .where(
                        EmailMessage.lead_id == lead.id,
                        EmailMessage.status.in_(
                            (
                                EmailMessageStatus.DRAFT.value,
                                EmailMessageStatus.APPROVED.value,
                                EmailMessageStatus.SCHEDULED.value,
                            )
                        ),
                    )
                    .values(
                        status=EmailMessageStatus.SKIPPED.value,
                        error="Cancelled due to repeated soft bounces",
                    )
                )
                db.add(
                    Event(
                        lead_id=lead.id,
                        type=EventType.BOUNCED.value,
                        payload={
                            "subject": mail.subject,
                            "reason": "3 soft bounces converted to hard bounce",
                        },
                        created_at=now,
                    )
                )
                db.commit()
                logger.warning("3rd soft bounce escalated to hard bounce for lead %s", lead.email)
            else:
                db.add(
                    Event(
                        lead_id=lead.id,
                        type=EventType.SOFT_BOUNCE.value,
                        payload={
                            "subject": mail.subject,
                            "soft_bounce_count": prior_count + 1,
                        },
                        created_at=now,
                    )
                )
                db.commit()
                logger.info("Soft bounce #%d logged for lead %s", prior_count + 1, lead.email)

    elif result.classification == InboundClassification.AUTO_REPLY:
        if lead:
            pending_msgs = db.scalars(
                select(EmailMessage).where(
                    EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(
                        (
                            EmailMessageStatus.APPROVED.value,
                            EmailMessageStatus.SCHEDULED.value,
                        )
                    ),
                )
            ).all()

            for p_msg in pending_msgs:
                base_time = p_msg.scheduled_at or now
                if base_time.tzinfo is None:
                    base_time = base_time.replace(tzinfo=UTC)
                p_msg.scheduled_at = base_time + timedelta(days=5)

            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.AUTO_REPLY.value,
                    payload={"delay_days": 5, "subject": mail.subject},
                    created_at=now,
                )
            )
            db.commit()
            logger.info("Auto-reply detected for lead %s; follow-ups delayed 5 days", lead.email)

    elif result.classification == InboundClassification.UNSUBSCRIBE:
        if lead:
            lead.status = LeadStatus.UNSUBSCRIBED.value
            add_suppression(db, target=lead.email, reason="unsubscribe")
            db.execute(
                update(EmailMessage)
                .where(
                    EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(
                        (
                            EmailMessageStatus.DRAFT.value,
                            EmailMessageStatus.APPROVED.value,
                            EmailMessageStatus.SCHEDULED.value,
                        )
                    ),
                )
                .values(
                    status=EmailMessageStatus.SKIPPED.value,
                    error="Cancelled due to unsubscribe",
                )
            )
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.UNSUBSCRIBED.value,
                    payload={"snippet": result.snippet},
                    created_at=now,
                )
            )
            reply_rec = Reply(
                lead_id=lead.id,
                email_message_id=result.matched_message.id if result.matched_message else None,
                from_email=mail.from_email,
                to_email=mail.to_email,
                subject=mail.subject,
                body=mail.body_text,
                snippet=result.snippet,
                classification=InboundClassification.UNSUBSCRIBE.value,
                is_positive_hint=False,
                created_at=now,
            )
            db.add(reply_rec)
            db.commit()
            logger.info("Unsubscribe processed for lead %s", lead.email)

    elif result.classification in (
        InboundClassification.REPLY_POSITIVE_HINT,
        InboundClassification.REPLY_OTHER,
    ):
        if lead:
            lead.status = LeadStatus.REPLIED.value
            db.execute(
                update(EmailMessage)
                .where(
                    EmailMessage.lead_id == lead.id,
                    EmailMessage.status.in_(
                        (
                            EmailMessageStatus.DRAFT.value,
                            EmailMessageStatus.APPROVED.value,
                            EmailMessageStatus.SCHEDULED.value,
                        )
                    ),
                )
                .values(
                    status=EmailMessageStatus.SKIPPED.value,
                    error="Cancelled due to lead reply",
                )
            )
            db.add(
                Event(
                    lead_id=lead.id,
                    type=EventType.REPLIED.value,
                    payload={
                        "snippet": result.snippet,
                        "is_positive_hint": result.is_positive_hint,
                    },
                    created_at=now,
                )
            )
            reply_rec = Reply(
                lead_id=lead.id,
                email_message_id=result.matched_message.id if result.matched_message else None,
                from_email=mail.from_email,
                to_email=mail.to_email,
                subject=mail.subject,
                body=mail.body_text,
                snippet=result.snippet,
                classification=result.classification.value,
                is_positive_hint=result.is_positive_hint,
                created_at=now,
            )
            db.add(reply_rec)
            db.commit()
            logger.info("Reply (%s) processed for lead %s", result.classification.value, lead.email)

    return result


def calculate_bounce_rates(db: Session, now: datetime) -> dict[str, float]:
    """Calculate bounce rates over the last 7 days and over the last 100 sent emails.

    Returns:
        Dict with 'bounce_rate_7d' and 'bounce_rate_last_100'.
    """
    from app.models.email_message import EmailMessage, EmailMessageStatus
    from app.models.event import Event, EventType
    from app.models.lead import Lead, LeadStatus
    from app.models.setting import Setting

    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)

    # 1. Over last 7 days
    start_7d = now - timedelta(days=7)
    sent_7d = (
        db.scalar(
            select(func.count(EmailMessage.id)).where(
                EmailMessage.sent_at >= start_7d,
                EmailMessage.status.in_(
                    (
                        EmailMessageStatus.SENT.value,
                        EmailMessageStatus.SENT_DRY_RUN.value,
                    )
                ),
            )
        )
        or 0
    )
    bounces_7d_raw = (
        db.scalar(
            select(func.count(func.distinct(Event.lead_id))).where(
                Event.created_at >= start_7d,
                Event.type == EventType.BOUNCED.value,
            )
        )
        or 0
    )
    bounces_7d = min(sent_7d, bounces_7d_raw)
    rate_7d = (bounces_7d / sent_7d * 100.0) if sent_7d > 0 else 0.0

    # 2. Over last 100 sent emails
    recent_sent = db.scalars(
        select(EmailMessage.lead_id)
        .where(
            EmailMessage.status.in_(
                (
                    EmailMessageStatus.SENT.value,
                    EmailMessageStatus.SENT_DRY_RUN.value,
                )
            )
        )
        .order_by(EmailMessage.sent_at.desc())
        .limit(100)
    ).all()

    total_recent = len(recent_sent)
    if total_recent > 0:
        bounces_100_raw = (
            db.scalar(
                select(func.count(Lead.id)).where(
                    Lead.id.in_(recent_sent),
                    Lead.status == LeadStatus.BOUNCED.value,
                )
            )
            or 0
        )
        bounces_100 = min(total_recent, bounces_100_raw)
        rate_100 = bounces_100 / total_recent * 100.0
    else:
        rate_100 = 0.0

    sample_size_ok = sent_7d >= 20 or total_recent >= 20

    db.merge(Setting(key="BOUNCE_RATE_7D", value=f"{rate_7d:.2f}"))
    db.merge(Setting(key="BOUNCE_RATE_LAST_100", value=f"{rate_100:.2f}"))
    db.commit()

    return {
        "bounce_rate_7d": rate_7d,
        "bounce_rate_last_100": rate_100,
        "sample_size_ok": sample_size_ok,
    }


def poll_inbox_and_process(
    reader: MailboxReader,
    db: Session,
    clock: Clock | None = None,
    notifier: Notifier | None = None,
) -> tuple[int, dict[str, float]]:
    """Poll newly arrived emails using checkpointed UID and calculate bounce rates.

    Returns:
        Tuple of (new_messages_count, bounce_rate_statistics).
    """
    from app.models.campaign import Campaign, CampaignStatus
    from app.models.setting import Setting
    from app.services.planner import get_setting

    clk = clock or system_clock
    now = clk.now()

    mb_id = getattr(getattr(reader, "mailbox", None), "id", None)
    if mb_id is not None:
        uid_key = f"LAST_INBOX_UID_MB_{mb_id}"
        last_uid_setting = db.scalar(select(Setting.value).where(Setting.key == uid_key))
        if last_uid_setting is None and mb_id == 1:
            # Fall back to legacy global key for primary mailbox if present
            last_uid_setting = db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID"))
    else:
        uid_key = "LAST_INBOX_UID"
        last_uid_setting = db.scalar(select(Setting.value).where(Setting.key == uid_key))

    last_uid = int(last_uid_setting) if last_uid_setting and last_uid_setting.isdigit() else 0

    new_mails = reader.fetch_new(since_uid=last_uid)
    for mail in new_mails:
        process_incoming_mail(mail, db, clock=clk)
        last_uid = max(last_uid, mail.uid)
        db.merge(Setting(key=uid_key, value=str(last_uid)))
        if uid_key != "LAST_INBOX_UID" and mb_id == 1:
            db.merge(Setting(key="LAST_INBOX_UID", value=str(last_uid)))
        db.commit()

    stats = calculate_bounce_rates(db, now)

    settings = get_settings()
    configured_threshold = float(
        get_setting(
            db,
            "BOUNCE_RATE_STOP_THRESHOLD_PERCENT",
            default=settings.BOUNCE_RATE_STOP_THRESHOLD_PERCENT,
        )
    )

    sample_size_ok = bool(stats.get("sample_size_ok", False))

    if sample_size_ok and (
        stats["bounce_rate_7d"] >= configured_threshold
        or stats["bounce_rate_last_100"] >= configured_threshold
    ):
        logger.critical(
            "BOUNCE RATE THRESHOLD EXCEEDED (7d: %.2f%%, last 100: %.2f%% >= %.2f%%)! "
            "Engaging emergency killswitch and pausing active campaigns immediately.",
            stats["bounce_rate_7d"],
            stats["bounce_rate_last_100"],
            configured_threshold,
        )
        from app.services.safety import is_killswitch_active, trigger_killswitch

        if not is_killswitch_active(db):
            peak_bounce = max(stats["bounce_rate_7d"], stats["bounce_rate_last_100"])
            trigger_killswitch(
                db,
                reason=(
                    f"Bounce rate spike ({peak_bounce:.2f}%) "
                    f"exceeded threshold of {configured_threshold:.2f}%"
                ),
                notifier=notifier,
                clock=clk,
            )
        else:
            db.execute(
                update(Campaign)
                .where(Campaign.status == CampaignStatus.ACTIVE.value)
                .values(status=CampaignStatus.PAUSED.value)
            )
            db.commit()

    return len(new_mails), stats
