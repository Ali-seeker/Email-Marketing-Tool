"""Lead CSV Importer service.

Handles flexible header resolution, data normalization, email validation,
deduplication against the file, campaign, and global suppression list,
timezone inference, rejects reporting, and audit event emission.
"""

import csv
import io
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import Clock, system_clock
from app.models.campaign import Campaign, CampaignStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.suppression import Suppression

# RFC 5322 compatible pragmatic email regex
EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)+$"
)

# Header aliases (normalized lowercase without spaces or punctuation)
HEADER_ALIASES: dict[str, list[str]] = {
    "email": ["email", "e-mail", "emailaddress", "email_address", "mail"],
    "first_name": ["firstname", "first_name", "first", "fname", "givenname"],
    "last_name": ["lastname", "last_name", "last", "lname", "surname", "familyname"],
    "company": ["company", "companyname", "company_name", "organization", "org", "business"],
    "website": ["website", "url", "domain", "web", "site", "homepage"],
    "title": ["title", "jobtitle", "job_title", "position", "role", "designation"],
    "country": ["country", "nation", "location"],
    "timezone": ["timezone", "tz", "time_zone", "time zone"],
    "personal_note": ["personalnote", "personal_note", "note", "notes", "comment", "comments"],
}

# Country to IANA Timezone mapping
COUNTRY_TIMEZONE_MAP: dict[str, str] = {
    "us": "America/New_York",
    "usa": "America/New_York",
    "united states": "America/New_York",
    "united states of america": "America/New_York",
    "uk": "Europe/London",
    "gb": "Europe/London",
    "united kingdom": "Europe/London",
    "great britain": "Europe/London",
    "england": "Europe/London",
    "uae": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai",
    "dubai": "Asia/Dubai",
    "ca": "America/Toronto",
    "canada": "America/Toronto",
    "au": "Australia/Sydney",
    "australia": "Australia/Sydney",
    "pk": "Asia/Karachi",
    "pakistan": "Asia/Karachi",
    "in": "Asia/Kolkata",
    "india": "Asia/Kolkata",
    "de": "Europe/Berlin",
    "germany": "Europe/Berlin",
    "deutschland": "Europe/Berlin",
}


@dataclass
class RejectItem:
    """Record of a rejected row during import."""

    row_number: int
    email: str | None
    reason: str
    raw_data: dict[str, str] = field(default_factory=dict)


@dataclass
class ImportReport:
    """Consolidated summary of a CSV import operation."""

    campaign_id: int
    campaign_name: str
    total_rows: int = 0
    imported: int = 0
    duplicates: int = 0
    suppressed: int = 0
    rejected: int = 0
    reject_items: list[RejectItem] = field(default_factory=list)
    rejects_file_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert report to dictionary format."""
        data = asdict(self)
        return data


def strip_invisible_chars(text: str) -> str:
    """Strip zero-width characters, non-breaking spaces, and unicode control junk."""
    if not text:
        return ""
    # Normalize unicode to NFKC
    normalized = unicodedata.normalize("NFKC", text)
    # Remove control characters and zero-width codes
    cleaned = "".join(
        ch for ch in normalized if unicodedata.category(ch)[0] != "C" or ch in ("\t", "\n", "\r")
    )
    return cleaned.strip()


def normalize_header(header: str) -> str:
    """Normalize raw CSV header name to canonical key."""
    cleaned = strip_invisible_chars(header).lower()
    cleaned = re.sub(r"[\s\-_]+", "", cleaned)
    for canonical, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = re.sub(r"[\s\-_]+", "", alias.lower())
            if cleaned == normalized_alias:
                return canonical
    return cleaned


def normalize_website(website: str | None) -> str | None:
    """Standardize website URLs to https://domain format."""
    if not website:
        return None
    val = strip_invisible_chars(website).strip()
    if not val:
        return None

    # Strip existing scheme if present
    val = re.sub(r"^https?://", "", val, flags=re.IGNORECASE)
    # Strip trailing slashes and paths if bare domain desired
    val = val.rstrip("/")
    if not val:
        return None
    return f"https://{val}"


def normalize_name(name: str | None) -> str | None:
    """Clean and title-case human names."""
    if not name:
        return None
    cleaned = strip_invisible_chars(name).strip()
    if not cleaned:
        return None
    # Capitalize each word segment preserving hyphens and apostrophes
    words = cleaned.split()
    capitalized_words = [w.capitalize() for w in words]
    return " ".join(capitalized_words)


def infer_timezone(country: str | None, explicit_tz: str | None) -> str | None:
    """Resolve timezone from explicit input or infer from country code/name."""
    if explicit_tz:
        cleaned_tz = strip_invisible_chars(explicit_tz).strip()
        if cleaned_tz:
            return cleaned_tz

    if not country:
        return None

    norm_country = strip_invisible_chars(country).strip().lower()
    return COUNTRY_TIMEZONE_MAP.get(norm_country)


def is_valid_email(email: str) -> bool:
    """Validate email format and length constraints."""
    if not email or len(email) > 320:
        return False
    parts = email.split("@")
    if len(parts) != 2 or len(parts[0]) > 64:
        return False
    return bool(EMAIL_REGEX.match(email))


def _resolve_campaign(db: Session, campaign_name: str, clock: Clock) -> Campaign:
    """Find existing campaign by name or create a new one."""
    stmt = select(Campaign).where(Campaign.name == campaign_name)
    campaign = db.scalar(stmt)
    if not campaign:
        campaign = Campaign(
            name=campaign_name,
            status=CampaignStatus.DRAFT.value,
            created_at=clock.now(),
        )
        db.add(campaign)
        db.flush()
    return campaign


def _write_rejects_file(
    rejects: list[RejectItem],
    clock: Clock,
    data_dir: Path | None = None,
) -> str | None:
    """Write rejected rows to data/rejects_<timestamp>.csv."""
    if not rejects:
        return None

    target_dir = data_dir or (Path(__file__).resolve().parent.parent.parent / "data")
    target_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = clock.now().strftime("%Y%m%d_%H%M%S")
    file_path = target_dir / f"rejects_{timestamp_str}.csv"

    fieldnames = ["row_number", "email", "reason", "raw_data"]
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in rejects:
            writer.writerow(
                {
                    "row_number": item.row_number,
                    "email": item.email or "",
                    "reason": item.reason,
                    "raw_data": str(item.raw_data),
                }
            )

    return str(file_path)


def import_leads(
    db: Session,
    campaign_name: str,
    csv_file: io.TextIOBase | str | Path,
    clock: Clock | None = None,
    data_dir: Path | None = None,
) -> ImportReport:
    """Import and validate leads from a CSV source into a campaign.

    Args:
        db: SQLAlchemy session.
        campaign_name: Target campaign name (auto-created if not present).
        csv_file: File-like object, string content, or Path to CSV file.
        clock: Optional Clock implementation (defaults to system_clock).
        data_dir: Optional custom directory for rejects CSV output.

    Returns:
        ImportReport containing counts, rejects, and file path.
    """
    active_clock = clock or system_clock
    campaign = _resolve_campaign(db, campaign_name, active_clock)

    # Resolve CSV reader
    close_file = False
    if isinstance(csv_file, (str, Path)) and Path(str(csv_file)).is_file():
        f = open(str(csv_file), encoding="utf-8-sig")
        reader = csv.reader(f)
        close_file = True
    elif isinstance(csv_file, str):
        f = io.StringIO(csv_file)
        reader = csv.reader(f)
    else:
        f = csv_file
        reader = csv.reader(f)

    try:
        rows = list(reader)
    finally:
        if close_file:
            f.close()

    if not rows:
        return ImportReport(
            campaign_id=campaign.id,
            campaign_name=campaign.name,
            total_rows=0,
        )

    # 1. Map header aliases
    raw_headers = rows[0]
    header_map: dict[int, str] = {}
    for idx, col in enumerate(raw_headers):
        canonical = normalize_header(col)
        header_map[idx] = canonical

    # 2. Pre-fetch existing emails for fast in-memory deduplication
    existing_campaign_emails: set[str] = set(
        db.scalars(select(Lead.email).where(Lead.campaign_id == campaign.id)).all()
    )

    seen_in_file: set[str] = set()
    reject_items: list[RejectItem] = []

    leads_to_insert: list[Lead] = []
    now_dt = active_clock.now()

    # Pre-extract all candidate emails to batch-query suppression table
    candidate_emails: set[str] = set()
    data_rows: list[tuple[int, dict[str, str]]] = []

    total_rows = 0
    for row_idx, row in enumerate(rows[1:], start=2):
        # Skip completely empty rows
        if not row or not any(str(cell).strip() for cell in row):
            continue

        total_rows += 1
        row_dict: dict[str, str] = {}
        for col_idx, cell in enumerate(row):
            if col_idx in header_map:
                row_dict[header_map[col_idx]] = cell

        raw_email = row_dict.get("email")
        if raw_email:
            cleaned_email = strip_invisible_chars(raw_email).strip().lower()
            if cleaned_email:
                candidate_emails.add(cleaned_email)

        data_rows.append((row_idx, row_dict))

    # Query suppressions for all candidate emails in one round-trip
    suppressed_emails: set[str] = set()
    if candidate_emails:
        suppressed_emails = set(
            db.scalars(
                select(Suppression.email).where(Suppression.email.in_(candidate_emails))
            ).all()
        )

    duplicates_count = 0
    suppressed_count = 0

    # 3. Process each row
    for row_number, row_dict in data_rows:
        raw_email = row_dict.get("email")

        # Check missing email
        if not raw_email or not strip_invisible_chars(raw_email).strip():
            reject_items.append(
                RejectItem(
                    row_number=row_number,
                    email=None,
                    reason="Missing email address",
                    raw_data=row_dict,
                )
            )
            continue

        email = strip_invisible_chars(raw_email).strip().lower()

        # Check malformed email
        if not is_valid_email(email):
            reject_items.append(
                RejectItem(
                    row_number=row_number,
                    email=email,
                    reason="Malformed email format",
                    raw_data=row_dict,
                )
            )
            continue

        # Check deduplication within file
        if email in seen_in_file:
            duplicates_count += 1
            continue
        seen_in_file.add(email)

        # Check suppression
        if email in suppressed_emails:
            suppressed_count += 1
            continue

        # Check deduplication against campaign
        if email in existing_campaign_emails:
            duplicates_count += 1
            continue

        # Valid new lead -> normalize remaining fields
        first_name = normalize_name(row_dict.get("first_name"))
        last_name = normalize_name(row_dict.get("last_name"))
        company = strip_invisible_chars(row_dict.get("company", "")).strip() or None
        website = normalize_website(row_dict.get("website"))
        title = strip_invisible_chars(row_dict.get("title", "")).strip() or None
        country = strip_invisible_chars(row_dict.get("country", "")).strip() or None
        personal_note = strip_invisible_chars(row_dict.get("personal_note", "")).strip() or None
        timezone_val = infer_timezone(country, row_dict.get("timezone"))

        lead = Lead(
            campaign_id=campaign.id,
            email=email,
            first_name=first_name,
            last_name=last_name,
            company=company,
            website=website,
            title=title,
            country=country,
            timezone=timezone_val,
            personal_note=personal_note,
            status=LeadStatus.NEW.value,
            created_at=now_dt,
            updated_at=now_dt,
        )
        leads_to_insert.append(lead)
        existing_campaign_emails.add(email)

    # 4. Batch persist leads
    if leads_to_insert:
        db.add_all(leads_to_insert)
        db.flush()

        # Emit imported Events for all new leads
        events = [
            Event(
                lead_id=lead.id,
                type=EventType.IMPORTED.value,
                payload={
                    "campaign_id": campaign.id,
                    "email": lead.email,
                    "company": lead.company,
                },
                created_at=now_dt,
            )
            for lead in leads_to_insert
        ]
        db.add_all(events)

    db.commit()

    # 5. Write rejects CSV if any
    rejects_path = _write_rejects_file(reject_items, active_clock, data_dir)

    return ImportReport(
        campaign_id=campaign.id,
        campaign_name=campaign.name,
        total_rows=total_rows,
        imported=len(leads_to_insert),
        duplicates=duplicates_count,
        suppressed=suppressed_count,
        rejected=len(reject_items),
        reject_items=reject_items,
        rejects_file_path=rejects_path,
    )
