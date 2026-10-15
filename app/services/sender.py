"""Outbound email dispatch service.

Implements SAFE BY DEFAULT architecture:
- MailSender abstract base interface.
- DryRunSender: strictly socket-free, persists to outbox table and filesystem when LIVE_MODE=false.
- SmtpSender: smtplib over SSL (or configurable for local test harness) when LIVE_MODE=true.
- FakeSender: in-memory delivery for test harnesses with error simulation.
- Plain-text, UTF-8 email formatting with unique Message-ID, Reply-To, and thread headers.
"""

import re
import smtplib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage as PyEmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.outbox import OutboxEmail

logger = get_logger(__name__)

KILLSWITCH_KEYWORDS = (
    "blocked",
    "suspended",
    "daily limit",
    "unusual activity",
    "quota exceeded",
    "account disabled",
)


@dataclass
class OutgoingMail:
    """Outbound email payload to be formatted and dispatched."""

    to_email: str
    from_email: str
    from_name: str
    subject: str
    body: str
    to_name: str | None = None
    reply_to: str | None = None
    in_reply_to: str | None = None
    references: str | None = None
    lead_id: int | None = None
    email_message_id: int | None = None
    step: int = 1
    extra_headers: dict[str, str] | None = None


@dataclass
class SendResult:
    """Outcome of an email dispatch attempt."""

    success: bool
    message_id: str | None = None
    error: str | None = None
    error_type: str | None = None  # None, "transient", "permanent", "auth", "killswitch"
    dry_run: bool = False


def build_email_message(mail: OutgoingMail) -> tuple[PyEmailMessage, str]:
    """Build a compliant RFC 5322 plain-text email message with threading headers.

    Rules:
    - Plain text, UTF-8 only.
    - No HTML, no tracking pixel, no attachments.
    - Proper From with display name.
    - Unique Message-ID using sender domain.
    - Threading headers (In-Reply-To, References) for step 2-4 follow-ups.
    - List-Unsubscribe header.
    """
    msg = PyEmailMessage()

    # Content
    msg.set_content(mail.body, subtype="plain", charset="utf-8")

    # Addressing with CRLF sanitization to prevent email header injection
    clean_from_name = (
        re.sub(r"[\r\n]+", " ", mail.from_name).strip() if mail.from_name else None
    )
    clean_to_name = re.sub(r"[\r\n]+", " ", mail.to_name).strip() if mail.to_name else None
    clean_from_email = re.sub(r"[\r\n]+", "", mail.from_email).strip()
    clean_to_email = re.sub(r"[\r\n]+", "", mail.to_email).strip()
    clean_subject = re.sub(r"[\r\n]+", " ", mail.subject).strip()

    if clean_from_name:
        msg["From"] = formataddr((clean_from_name, clean_from_email))
    else:
        msg["From"] = clean_from_email

    if clean_to_name:
        msg["To"] = formataddr((clean_to_name, clean_to_email))
    else:
        msg["To"] = clean_to_email

    msg["Subject"] = clean_subject
    reply_to_addr = (
        re.sub(r"[\r\n]+", "", mail.reply_to).strip()
        if mail.reply_to
        else clean_from_email
    )
    msg["Reply-To"] = reply_to_addr
    msg["Date"] = formatdate(localtime=False)

    # Domain for Message-ID
    sender_domain = "example.com"
    if "@" in clean_from_email:
        raw_dom = clean_from_email.split("@")[1].strip()
        cleaned_dom = re.sub(r"[\r\n\s<>\"']+", "", raw_dom)
        if cleaned_dom and "." in cleaned_dom:
            sender_domain = cleaned_dom

    generated_msg_id = make_msgid(domain=sender_domain)
    msg["Message-ID"] = generated_msg_id

    # Threading headers for follow-ups
    if mail.step > 1 or mail.in_reply_to:
        if mail.in_reply_to:
            msg["In-Reply-To"] = mail.in_reply_to
        if mail.references:
            msg["References"] = mail.references

    # List-Unsubscribe header
    msg["List-Unsubscribe"] = f"<mailto:{clean_from_email}?subject=unsubscribe>"

    # Extra custom headers if provided
    if mail.extra_headers:
        for k, v in mail.extra_headers.items():
            if k not in msg:
                msg[k] = v

    return msg, generated_msg_id


class MailSender(ABC):
    """Abstract outbound mail sender interface."""

    @abstractmethod
    def send(self, message: OutgoingMail) -> SendResult:
        """Deliver an email message or simulate delivery.

        Args:
            message: OutgoingMail payload.

        Returns:
            SendResult detailing success status, message_id, or error classification.
        """
        pass


class FakeSender(MailSender):
    """In-memory sender for deterministic testing of worker loops and error handling."""

    def __init__(self) -> None:
        self.sent_messages: list[OutgoingMail] = []
        self.fail_mode: str | None = None  # "transient", "permanent", "auth", "killswitch"
        self.fail_error: str | None = None
        self.fail_count: int = 0
        self.max_failures: int | None = None
        self.next_result: SendResult | None = None
        self.results_queue: list[SendResult] = []

    def send(self, message: OutgoingMail) -> SendResult:
        """Simulate sending with configurable error injections."""
        self.sent_messages.append(message)
        if self.next_result is not None:
            res = self.next_result
            self.next_result = None
            return res
        if self.results_queue:
            return self.results_queue.pop(0)

        if self.fail_mode is not None:
            if self.max_failures is None or self.fail_count < self.max_failures:
                self.fail_count += 1
                err_msg = self.fail_error or f"Simulated {self.fail_mode} failure"
                return SendResult(
                    success=False,
                    error=err_msg,
                    error_type=self.fail_mode,
                )

        # Successful simulation
        has_at = "@" in message.from_email
        sender_domain = message.from_email.split("@")[1] if has_at else "test.com"
        msg_id = make_msgid(domain=sender_domain)
        return SendResult(success=True, message_id=msg_id)


class DryRunSender(MailSender):
    """Safe sender used when LIVE_MODE=false.

    Strictly isolated from network sockets. Writes full rendered email to the database outbox table
    and local filesystem outbox directory.
    """

    def __init__(
        self,
        db: Session | None = None,
        outbox_dir: Path | str = "data/outbox",
    ) -> None:
        self._db = db
        self._outbox_dir = Path(outbox_dir)

    def send(self, message: OutgoingMail) -> SendResult:
        """Render and persist email locally without opening network connections."""
        email_obj, msg_id = build_email_message(message)
        raw_eml = email_obj.as_string()

        # 1. Write to local filesystem outbox
        try:
            self._outbox_dir.mkdir(parents=True, exist_ok=True)
            clean_id = msg_id.strip("<>").replace("@", "_at_")
            outbox_file = self._outbox_dir / f"{clean_id}.eml"
            outbox_file.write_text(raw_eml, encoding="utf-8")
        except Exception as e:
            logger.warning("Could not write outbox file: %s", e)

        # 2. Persist to outbox_emails database table
        close_session = False
        db = self._db
        if db is None:
            db = SessionLocal()
            close_session = True

        try:
            headers_dict = dict(email_obj.items())
            record = OutboxEmail(
                email_message_id=message.email_message_id,
                to_email=message.to_email,
                from_email=message.from_email,
                subject=message.subject,
                body=message.body,
                headers=headers_dict,
                raw_eml=raw_eml,
                status="sent (dry-run)",
                created_at=datetime.now(UTC),
            )
            db.add(record)
            db.commit()
        finally:
            if close_session:
                db.close()

        logger.info(
            "[DRY-RUN] Outbound email to %s logged locally (Message-ID: %s)",
            message.to_email,
            msg_id,
        )
        return SendResult(success=True, message_id=msg_id, dry_run=True)


class SmtpSender(MailSender):
    """Live outbound SMTP sender over SSL / TLS.

    Strictly used only when LIVE_MODE=true (or targeted at localhost test harnesses).
    """

    def __init__(
        self,
        host: str = "smtp.gmail.com",
        port: int = 465,
        username: str | None = None,
        password: str | None = None,
        use_ssl: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.timeout = timeout

    def send(self, message: OutgoingMail) -> SendResult:
        """Transmit email message through SMTP server."""
        email_obj, msg_id = build_email_message(message)

        try:
            if self.use_ssl:
                server: smtplib.SMTP = smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout)
            else:
                server = smtplib.SMTP(self.host, self.port, timeout=self.timeout)
                try:
                    server.starttls()
                except smtplib.SMTPNotSupportedError:
                    pass

            with server:
                if self.username and self.password:
                    clean_user = self.username.strip()
                    clean_pwd = self.password.replace(" ", "").strip()
                    try:
                        server.login(clean_user, clean_pwd)
                    except smtplib.SMTPServerDisconnected as e:
                        raise smtplib.SMTPAuthenticationError(
                            535,
                            b"5.7.8 Username and Password not accepted by Gmail (BadCredentials). Please verify your 16-character Google App Password and account email.",
                        ) from e
                server.send_message(email_obj)

            logger.info("Email to %s sent successfully via SMTP (%s)", message.to_email, msg_id)
            return SendResult(success=True, message_id=msg_id)

        except smtplib.SMTPAuthenticationError as e:
            logger.error("SMTP Authentication failure: %s", e)
            return SendResult(success=False, error=str(e), error_type="auth")

        except smtplib.SMTPResponseException as e:
            raw_err = e.smtp_error
            err_detail = raw_err.decode() if isinstance(raw_err, bytes) else str(raw_err)
            err_text = f"SMTP error ({e.smtp_code}): {err_detail}"
            lower_err = err_text.lower()
            if any(k in lower_err for k in KILLSWITCH_KEYWORDS):
                err_type = "killswitch"
            elif 400 <= e.smtp_code < 500:
                err_type = "transient"
            else:
                err_type = "permanent"

            logger.error("SMTP response error: %s (%s)", err_text, err_type)
            return SendResult(success=False, error=err_text, error_type=err_type)

        except (
            smtplib.SMTPServerDisconnected,
            TimeoutError,
            ConnectionRefusedError,
            OSError,
        ) as e:
            err_text = f"SMTP network/connection error: {e}"
            logger.warning("SMTP transient failure: %s", err_text)
            return SendResult(success=False, error=err_text, error_type="transient")

        except Exception as e:
            err_text = f"Unexpected SMTP dispatch error: {e}"
            lower_err = err_text.lower()
            if any(k in lower_err for k in KILLSWITCH_KEYWORDS) or "auth" in lower_err:
                err_type = "killswitch"
            else:
                err_type = "permanent"
            logger.error("SMTP error: %s", err_text)
            return SendResult(success=False, error=err_text, error_type=err_type)


def is_live_mode_active(db: Session | None = None) -> bool:
    """Return whether LIVE_MODE is currently enabled via DB override or .env."""
    if db is not None:
        from sqlalchemy import select
        from app.models.setting import Setting
        val = db.scalar(select(Setting.value).where(Setting.key == "LIVE_MODE"))
        if val is not None:
            return val.strip().lower() in ("true", "1", "yes")
    return bool(get_settings().LIVE_MODE)


def get_sender(
    db: Session | None = None,
    override_live_mode: bool | None = None,
    mailbox: Any | None = None,
) -> MailSender:
    """Factory creating the appropriate sender according to LIVE_MODE safety setting and mailbox."""
    cfg = get_settings()
    is_live = is_live_mode_active(db) if override_live_mode is None else override_live_mode

    if not is_live:
        return DryRunSender(db=db)

    # 1. If a specific Mailbox instance is passed, use its credentials
    if mailbox is not None:
        return SmtpSender(
            host=mailbox.smtp_host,
            port=mailbox.smtp_port,
            username=mailbox.smtp_username,
            password=mailbox.smtp_password,
            use_ssl=mailbox.smtp_use_ssl,
        )

    # 2. Fallback to global config (.env)
    pwd = cfg.SMTP_PASSWORD.get_secret_value() if cfg.SMTP_PASSWORD else None
    return SmtpSender(
        host=cfg.SMTP_HOST,
        port=cfg.SMTP_PORT,
        username=cfg.SMTP_USERNAME,
        password=pwd,
        use_ssl=cfg.SMTP_PORT == 465,
    )

