"""Inbound email classification and lead matching service.

Categorizes all incoming messages into exactly one of:
- hard_bounce
- soft_bounce
- auto_reply
- unsubscribe
- reply_positive_hint
- reply_other
- unrelated

Implements strict lead matching:
1. By In-Reply-To / References matching a Message-ID sent to the lead.
2. Fall back to From address matching lead email (case-insensitive).
3. Never match by name.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.email_message import EmailMessage
from app.models.lead import Lead
from app.services.inbox import IncomingMail

if TYPE_CHECKING:
    pass


class InboundClassification(StrEnum):
    """Exhaustive classification categories for incoming emails."""

    HARD_BOUNCE = "hard_bounce"
    SOFT_BOUNCE = "soft_bounce"
    AUTO_REPLY = "auto_reply"
    UNSUBSCRIBE = "unsubscribe"
    REPLY_POSITIVE_HINT = "reply_positive_hint"
    REPLY_OTHER = "reply_other"
    UNRELATED = "unrelated"


# Patterns for extracting recipient from bounce reports
FINAL_RECIPIENT_REGEX = re.compile(
    r"(?:Final-Recipient|Original-Recipient):\s*(?:rfc822;)?\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?",
    re.IGNORECASE,
)
FAILED_RECIP_BODY_REGEX = re.compile(
    r"(?:failed to deliver to|was not delivered to|could not be delivered to"
    r"|recipient rejected|unknown user|user unknown|mailbox unavailable|no such user)"
    r"[^@\n]*?<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?",
    re.IGNORECASE,
)
GENERIC_EMAIL_REGEX = re.compile(
    r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b",
    re.IGNORECASE,
)

# Status code patterns for DSN (Delivery Status Notification)
STATUS_CODE_REGEX = re.compile(r"\bStatus:\s*([45]\.\d+\.\d+)", re.IGNORECASE)
DIAGNOSTIC_CODE_REGEX = re.compile(r"\bDiagnostic-Code:[^\n]*?\b([45]\d{2})\b", re.IGNORECASE)
GENERIC_STATUS_REGEX = re.compile(r"\b([45]\.\d+\.\d+)\b")

# Soft bounce keywords indicating temporary failure or full mailbox
SOFT_BOUNCE_KEYWORDS = (
    "mailbox full",
    "mailbox is full",
    "quota exceeded",
    "exceeded storage quota",
    "over quota",
    "temporary failure",
    "try again later",
    "greylisted",
    "greylisting",
    "server temporarily unavailable",
    "mailbox temporarily disabled",
)

# Auto-reply header values and phrases
AUTO_REPLY_PHRASES = (
    "out of office",
    "automatic reply",
    "auto-reply",
    "away from the office",
    "away from office",
    "vacation response",
    "on annual leave",
    "currently out of office",
    "currently away",
    "limited access to email",
    "will respond when i return",
    "will reply upon my return",
    "maternity leave",
    "paternity leave",
)

# Unsubscribe trigger phrases
UNSUBSCRIBE_PHRASES = (
    "unsubscribe",
    "remove me",
    "remove us",
    "please remove",
    "take me off your list",
    "take me off this list",
    "take us off",
    "stop emailing",
    "stop contacting",
    "do not contact",
    "do not email",
    "dont contact",
    "dont email",
    "lose my email",
    "opt out",
    "opt-out",
)

# Positive interest hint signals matching prompt specifications
POSITIVE_HINT_PATTERNS = (
    re.compile(r"\binterested\b", re.IGNORECASE),
    re.compile(r"\blet'?s\s+talk\b", re.IGNORECASE),
    re.compile(r"\bcall\b", re.IGNORECASE),
    re.compile(r"\bschedule\b", re.IGNORECASE),
    re.compile(r"\bsend\s+details\b", re.IGNORECASE),
    re.compile(r"\bcalendly\b", re.IGNORECASE),
)

POSITIVE_HINT_PHRASES = (
    "interested",
    "let's talk",
    "lets talk",
    "call me",
    "schedule",
    "send details",
    "send more details",
    "send over details",
    "send information",
    "send me info",
    "calendly",
    "happy to chat",
    "sounds interesting",
    "sounds good",
    "let's connect",
    "lets connect",
    "book time",
    "what time works",
    "hop on a call",
    "jump on a call",
    "give me a call",
)

# Quoted history split patterns
QUOTE_HEADERS = (
    re.compile(r"^On\s.+wrote:\s*$", re.MULTILINE),
    re.compile(r"^-+\s*Original Message\s*-+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^From:\s.+Sent:\s.+", re.MULTILINE),
    re.compile(r"^_{10,}", re.MULTILINE),
)


def strip_quoted_history(body: str) -> str:
    """Remove forwarded/quoted history lines from email body."""
    lines = body.splitlines()
    clean_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        # Cut off upon hitting common reply quotation delimiters
        if stripped.startswith(">"):
            continue
        if any(h.match(line) for h in QUOTE_HEADERS):
            break
        clean_lines.append(line)

    result = "\n".join(clean_lines).strip()
    return result if result else body.strip()


def extract_snippet(body: str, max_chars: int = 300) -> str:
    """Extract first N characters of stripped message body."""
    cleaned = strip_quoted_history(body)
    snippet = cleaned.replace("\r", " ").replace("\n", " ").strip()
    snippet = re.sub(r"\s+", " ", snippet)
    return snippet[:max_chars]


def extract_bounce_recipient(mail: IncomingMail) -> str | None:
    """Extract failed target recipient email from a bounce notification."""
    text_corpus = f"{mail.subject}\n{mail.body_text}"

    # 1. Headers / DSN delivery-status fields
    for field_name in ("Final-Recipient", "Original-Recipient"):
        val = mail.headers.get(field_name)
        if val:
            m = FINAL_RECIPIENT_REGEX.search(f"{field_name}: {val}")
            if m:
                return m.group(1).lower()

    # 2. Regex in raw body / DSN part
    m = FINAL_RECIPIENT_REGEX.search(mail.body_text)
    if m:
        return m.group(1).lower()

    # 3. Contextual error phrasing
    m_body = FAILED_RECIP_BODY_REGEX.search(text_corpus)
    if m_body:
        return m_body.group(1).lower()

    # 4. Fallback search for any email address in the body excluding the postmaster/mailer-daemon
    all_emails = GENERIC_EMAIL_REGEX.findall(mail.body_text)
    for e in all_emails:
        e_lower = e.lower()
        if not any(
            daemon in e_lower
            for daemon in (
                "mailer-daemon",
                "postmaster",
                "daemon",
                "googlemail.com",
                "bounce",
                "localhost",
            )
        ):
            return e_lower

    return None


def is_bounce_sender_or_structure(mail: IncomingMail) -> bool:
    """Check if email originated from a mailer daemon, MTA, or DSN structure."""
    from_lower = mail.from_email.lower()
    from_name_lower = (mail.from_name or "").lower()

    if any(
        kw in from_lower
        for kw in ("mailer-daemon", "postmaster", "mail-daemon", "daemon@", "bounce@")
    ):
        return True
    if any(kw in from_name_lower for kw in ("mailer-daemon", "mail delivery", "postmaster")):
        return True
    if mail.is_multipart_report:
        return True
    if "delivery-status" in mail.content_type.lower():
        return True

    # Subject indicators
    subj_lower = mail.subject.lower()
    if any(
        b_kw in subj_lower
        for b_kw in (
            "delivery status notification",
            "undeliverable:",
            "mail delivery failed",
            "returned mail:",
            "delivery failure",
            "failure notice",
        )
    ):
        return True

    return False


def is_auto_reply(mail: IncomingMail) -> bool:
    """Determine if incoming message is an automated vacation/out-of-office notification."""
    # Check headers
    auto_submitted = mail.headers.get("Auto-Submitted", "").lower().strip()
    if auto_submitted and auto_submitted != "no":
        return True

    if mail.headers.get("X-Autoreply", "").lower().strip() == "yes":
        return True

    precedence = mail.headers.get("Precedence", "").lower().strip()
    if precedence in ("bulk", "auto_reply", "junk"):
        return True

    # Check subject and body
    subj_lower = mail.subject.lower()
    if any(
        subj_lower.startswith(prefix)
        for prefix in (
            "automatic reply:",
            "auto-reply:",
            "out of office:",
            "ooo:",
            "vacation:",
            "away:",
        )
    ):
        return True

    body_lower = mail.body_text.lower()
    if any(phrase in body_lower for phrase in AUTO_REPLY_PHRASES):
        return True

    return False


def is_unsubscribe(body_text: str) -> bool:
    """Detect if reply requests removal, stop, or unsubscribing."""
    body_lower = body_text.lower()
    if any(phrase in body_lower for phrase in UNSUBSCRIBE_PHRASES):
        return True

    # Compound check: "not interested" + ("stop" or "remove")
    if "not interested" in body_lower and ("stop" in body_lower or "remove" in body_lower):
        return True

    return False


def is_positive_hint(body_text: str, subject: str = "") -> bool:
    """Detect positive interest signals indicating an opportunity or desire to speak."""
    combined = f"{subject}\n{body_text}"
    if any(pattern.search(combined) for pattern in POSITIVE_HINT_PATTERNS):
        return True
    combined_lower = combined.lower()
    return any(phrase in combined_lower for phrase in POSITIVE_HINT_PHRASES)


@dataclass
class ClassificationResult:
    """Full outcome of classifying and matching an inbound email."""

    classification: InboundClassification
    matched_lead: Lead | None
    matched_message: EmailMessage | None
    bounce_recipient: str | None
    snippet: str
    is_positive_hint: bool


def match_mail_to_lead(
    mail: IncomingMail,
    db: Session,
    bounce_recipient: str | None = None,
) -> tuple[Lead | None, EmailMessage | None]:
    """Resolve lead and sequence message matching this incoming email.

    Matching rules (strictly enforced):
    1. If bounce_recipient provided, match Lead.email == bounce_recipient.
    2. Check In-Reply-To and References matching a Message-ID we sent.
    3. Fall back to From email address matching Lead.email (case-insensitive).
    4. Never match by name.
    """
    # 1. Match by extracted bounce recipient
    if bounce_recipient:
        lead = db.scalar(
            select(Lead).where(func.lower(Lead.email) == bounce_recipient.strip().lower())
        )
        if lead:
            latest_msg = db.scalar(
                select(EmailMessage)
                .where(EmailMessage.lead_id == lead.id)
                .order_by(EmailMessage.step.desc(), EmailMessage.id.desc())
            )
            return lead, latest_msg

    # 2. Match by In-Reply-To / References Message-IDs
    candidate_refs = []
    if mail.in_reply_to:
        candidate_refs.append(mail.in_reply_to)
    for ref in mail.references:
        if ref not in candidate_refs:
            candidate_refs.append(ref)

    for ref_id in candidate_refs:
        clean_ref = ref_id.strip()
        matched_msg = db.scalar(select(EmailMessage).where(EmailMessage.message_id == clean_ref))
        if matched_msg:
            lead = matched_msg.lead or db.get(Lead, matched_msg.lead_id)
            return lead, matched_msg

    # 3. Fall back to From email address
    from_addr = mail.from_email.strip().lower()
    if from_addr:
        lead = db.scalar(select(Lead).where(func.lower(Lead.email) == from_addr))
        if lead:
            latest_msg = db.scalar(
                select(EmailMessage)
                .where(EmailMessage.lead_id == lead.id)
                .order_by(EmailMessage.step.desc(), EmailMessage.id.desc())
            )
            return lead, latest_msg

    return None, None


def classify_inbound_mail(
    mail: IncomingMail,
    db: Session,
) -> ClassificationResult:
    """Classify incoming email into exactly one category and link to lead if applicable."""
    body_snippet = extract_snippet(mail.body_text)

    # 1. Check Bounce Signals
    if is_bounce_sender_or_structure(mail):
        bounce_recipient = extract_bounce_recipient(mail)
        lead, msg = match_mail_to_lead(mail, db, bounce_recipient=bounce_recipient)

        # Detect status code in body or headers
        combined_text = f"{mail.headers}\n{mail.body_text}"
        status_match = STATUS_CODE_REGEX.search(combined_text) or GENERIC_STATUS_REGEX.search(
            combined_text
        )
        status_code = status_match.group(1) if status_match else None

        # Check soft bounce criteria (4.x.x status code or explicit mailbox full keywords)
        lower_body = mail.body_text.lower()
        is_soft = False
        if status_code and status_code.startswith("4."):
            is_soft = True
        elif any(kw in lower_body for kw in SOFT_BOUNCE_KEYWORDS):
            is_soft = True

        classification = (
            InboundClassification.SOFT_BOUNCE if is_soft else InboundClassification.HARD_BOUNCE
        )
        return ClassificationResult(
            classification=classification,
            matched_lead=lead,
            matched_message=msg,
            bounce_recipient=bounce_recipient,
            snippet=body_snippet,
            is_positive_hint=False,
        )

    # 2. Not a bounce: Attempt lead match by In-Reply-To / References or From email
    matched_lead, matched_msg = match_mail_to_lead(mail, db)

    # If message cannot be matched to any lead in our database, it is unrelated
    if matched_lead is None:
        return ClassificationResult(
            classification=InboundClassification.UNRELATED,
            matched_lead=None,
            matched_message=None,
            bounce_recipient=None,
            snippet="",
            is_positive_hint=False,
        )

    # Ignore ColdReach internal verification emails
    if mail.subject.strip().lower().startswith("[coldreach]"):
        return ClassificationResult(
            classification=InboundClassification.UNRELATED,
            matched_lead=None,
            matched_message=None,
            bounce_recipient=None,
            snippet="",
            is_positive_hint=False,
        )

    # If sender and recipient are the same email (self-test) without reply markers, ignore outgoing copy
    if (
        mail.from_email.strip().lower() == mail.to_email.strip().lower()
        and not mail.in_reply_to
        and not mail.references
        and not mail.subject.strip().lower().startswith("re:")
    ):
        return ClassificationResult(
            classification=InboundClassification.UNRELATED,
            matched_lead=None,
            matched_message=None,
            bounce_recipient=None,
            snippet="",
            is_positive_hint=False,
        )

    # 3. Check Auto-Reply
    if is_auto_reply(mail):
        return ClassificationResult(
            classification=InboundClassification.AUTO_REPLY,
            matched_lead=matched_lead,
            matched_message=matched_msg,
            bounce_recipient=None,
            snippet=body_snippet,
            is_positive_hint=False,
        )

    # 4. Check Unsubscribe
    if is_unsubscribe(mail.body_text):
        return ClassificationResult(
            classification=InboundClassification.UNSUBSCRIBE,
            matched_lead=matched_lead,
            matched_message=matched_msg,
            bounce_recipient=None,
            snippet=body_snippet,
            is_positive_hint=False,
        )

    # 5. Check Positive Reply Hint
    if is_positive_hint(mail.body_text, mail.subject):
        return ClassificationResult(
            classification=InboundClassification.REPLY_POSITIVE_HINT,
            matched_lead=matched_lead,
            matched_message=matched_msg,
            bounce_recipient=None,
            snippet=body_snippet,
            is_positive_hint=True,
        )

    # 6. Any other genuine human reply
    return ClassificationResult(
        classification=InboundClassification.REPLY_OTHER,
        matched_lead=matched_lead,
        matched_message=matched_msg,
        bounce_recipient=None,
        snippet=body_snippet,
        is_positive_hint=False,
    )
