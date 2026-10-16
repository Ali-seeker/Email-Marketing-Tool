"""ColdReach Diagnostic Health Check Service.

Implements comprehensive verification of system state, configuration integrity,
hard safety limits, database health, templates, and delivery infrastructure.
"""

import imaplib
import smtplib
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.campaign import Campaign
from app.models.lead import Lead, LeadStatus
from app.models.sequence import SequenceStep
from app.models.setting import Setting
from app.models.suppression import Suppression
from app.services.linter import lint_email
from app.services.planner import (
    HARD_MAX_LIMIT,
    get_outreach_start_date,
    get_ramp_up_cap,
    get_setting,
)
from app.services.templates import build_signature_block, load_default_sequence

CheckStatus = Literal["PASS", "WARN", "FAIL"]


@dataclass
class CheckItem:
    """Individual diagnostic checklist item result."""

    name: str
    status: CheckStatus
    message: str


@dataclass
class DoctorReport:
    """Consolidated checklist report from the doctor diagnostic inspection."""

    items: list[CheckItem]

    @property
    def has_failure(self) -> bool:
        """Return True if any check failed."""
        return any(item.status == "FAIL" for item in self.items)

    @property
    def has_warning(self) -> bool:
        """Return True if any check raised a warning."""
        return any(item.status == "WARN" for item in self.items)


def run_doctor_checks(
    db: Session,
    live_check: bool = False,
    data_dir: Path | None = None,
) -> DoctorReport:
    """Execute all system health and pre-flight checks."""
    settings = get_settings()
    items: list[CheckItem] = []
    base_dir = Path(__file__).resolve().parent.parent.parent
    active_data_dir = data_dir or (base_dir / "data")

    # 1. .env present and complete
    env_file = base_dir / ".env"
    if not env_file.is_file():
        items.append(
            CheckItem(
                name=".env Configuration",
                status="FAIL",
                message=".env file not found in repository root.",
            )
        )
    else:
        required_keys = ["API_TOKEN", "LIVE_MODE"]
        missing_keys = []
        with open(env_file, encoding="utf-8") as f:
            env_content = f.read()
        for k in required_keys:
            if k not in env_content:
                missing_keys.append(k)

        if missing_keys:
            items.append(
                CheckItem(
                    name=".env Configuration",
                    status="FAIL",
                    message=f".env is missing required keys: {', '.join(missing_keys)}",
                )
            )
        else:
            items.append(
                CheckItem(
                    name=".env Configuration",
                    status="PASS",
                    message=".env present and contains required configuration keys.",
                )
            )

    # 2. LIVE_MODE state
    if settings.LIVE_MODE:
        items.append(
            CheckItem(
                name="LIVE_MODE State",
                status="WARN",
                message="LIVE_MODE=true (OUTBOUND SMTP DISPATCH ACTIVE - real emails will be sent)",
            )
        )
    else:
        items.append(
            CheckItem(
                name="LIVE_MODE State",
                status="PASS",
                message="LIVE_MODE=false (SAFE BY DEFAULT - dry run active, zero real emails)",
            )
        )

    # 3. OUTREACH_START_DATE set
    try:
        start_date = get_outreach_start_date(db)
        items.append(
            CheckItem(
                name="Outreach Start Date",
                status="PASS",
                message=f"OUTREACH_START_DATE is configured: {start_date.isoformat()}",
            )
        )
    except Exception as e:
        items.append(
            CheckItem(
                name="Outreach Start Date",
                status="FAIL",
                message=f"OUTREACH_START_DATE invalid or unset: {e}",
            )
        )
        start_date = date.today()

    # 4. Ramp-up sane
    try:
        today = date.today()
        ramp_cap = get_ramp_up_cap(today, start_date)
        if 0 <= ramp_cap <= HARD_MAX_LIMIT:
            items.append(
                CheckItem(
                    name="Ramp-up Schedule",
                    status="PASS",
                    message=(
                        f"Current daily ramp cap is {ramp_cap} emails/day (<= {HARD_MAX_LIMIT})."
                    ),
                )
            )
        else:
            items.append(
                CheckItem(
                    name="Ramp-up Schedule",
                    status="FAIL",
                    message=f"Ramp cap {ramp_cap} exceeds hard limit {HARD_MAX_LIMIT}.",
                )
            )
    except Exception as e:
        items.append(
            CheckItem(
                name="Ramp-up Schedule",
                status="FAIL",
                message=f"Ramp-up calculation error: {e}",
            )
        )

    # 5. Hard limits intact
    hard_max_val = get_setting(db, "HARD_MAX_PER_DAY", default=HARD_MAX_LIMIT)
    min_gap_val = get_setting(db, "MIN_GAP_SECONDS", default=120)
    bounce_stop_val = get_setting(db, "BOUNCE_RATE_STOP_THRESHOLD_PERCENT", default=2.0)

    try:
        hard_max_int = int(hard_max_val)
        min_gap_int = int(min_gap_val)
        bounce_stop_float = float(bounce_stop_val)

        limits_ok = (
            hard_max_int <= HARD_MAX_LIMIT and min_gap_int >= 120 and bounce_stop_float <= 2.0
        )
        if limits_ok:
            items.append(
                CheckItem(
                    name="Hard Safety Limits",
                    status="PASS",
                    message=(
                        f"Hard limits intact: MAX_DAY={hard_max_int} (<=30), "
                        f"MIN_GAP={min_gap_int}s (>=120s), "
                        f"BOUNCE_STOP={bounce_stop_float}% (<=2.0%)."
                    ),
                )
            )
        else:
            items.append(
                CheckItem(
                    name="Hard Safety Limits",
                    status="FAIL",
                    message=(
                        f"Safety constraint violation: MAX_DAY={hard_max_int}, "
                        f"MIN_GAP={min_gap_int}s, BOUNCE_STOP={bounce_stop_float}%"
                    ),
                )
            )
    except Exception as e:
        items.append(
            CheckItem(
                name="Hard Safety Limits",
                status="FAIL",
                message=f"Could not parse safety limits: {e}",
            )
        )

    # 6. Database writable and backed up
    try:
        # Test write operation with a temporary Setting key
        test_key = "__doctor_write_check__"
        db.add(Setting(key=test_key, value="ok"))
        db.flush()
        db.rollback()

        # Check backup folder
        backup_dir = active_data_dir / "backups"
        backup_files = list(backup_dir.glob("*.db")) if backup_dir.is_dir() else []
        if backup_files:
            items.append(
                CheckItem(
                    name="Database & Backups",
                    status="PASS",
                    message=(
                        f"Database is writable. Found {len(backup_files)} "
                        f"backup(s) in {backup_dir.name}."
                    ),
                )
            )
        else:
            items.append(
                CheckItem(
                    name="Database & Backups",
                    status="WARN",
                    message="Database is writable, but zero backups found in data/backups.",
                )
            )
    except Exception as e:
        items.append(
            CheckItem(
                name="Database & Backups",
                status="FAIL",
                message=f"Database write verification failed: {e}",
            )
        )

    # 7. Signature and opt-out text configured
    sig_block = build_signature_block()
    has_name = bool(settings.SENDER_NAME and settings.SENDER_NAME.strip())
    has_company = bool(settings.SENDER_COMPANY and settings.SENDER_COMPANY.strip())
    has_website = bool(settings.SENDER_WEBSITE and settings.SENDER_WEBSITE.strip())

    if has_name and has_company and has_website and len(sig_block.strip()) > 10:
        items.append(
            CheckItem(
                name="Sender Signature & Opt-Out",
                status="PASS",
                message=(
                    f"Configured: {settings.SENDER_NAME} "
                    f"({settings.SENDER_COMPANY}, {settings.SENDER_WEBSITE})"
                ),
            )
        )
    else:
        items.append(
            CheckItem(
                name="Sender Signature & Opt-Out",
                status="FAIL",
                message="Incomplete sender details in settings (SENDER_NAME/COMPANY/WEBSITE).",
            )
        )

    # 8. Sequence templates pass the linter
    linter_failures = []
    # Test bundled default template
    default_campaign = db.scalar(select(Campaign).limit(1))
    if default_campaign:
        step_records = db.scalars(
            select(SequenceStep).where(SequenceStep.campaign_id == default_campaign.id)
        ).all()
        if not step_records:
            step_records = load_default_sequence(default_campaign.id, db)
    else:
        # Create a transient campaign for lint verification
        dummy_camp = Campaign(name="__doctor_camp__")
        db.add(dummy_camp)
        db.flush()
        step_records = load_default_sequence(dummy_camp.id, db)

    from app.services.templates import build_template_context, render_template_string

    camp_obj = default_campaign or Campaign(id=1, name="Check Campaign", niche="software")
    dummy_lead = Lead(
        id=1,
        campaign_id=camp_obj.id,
        first_name="John",
        last_name="Doe",
        company="Acme Corp",
        email="john@example.com",
        personal_note="Saw your team's launch on Hacker News and loved the clean execution.",
    )
    ctx = build_template_context(dummy_lead, camp_obj)

    for step_rec in step_records:
        for s_idx, subj in enumerate(step_rec.subjects):
            for b_idx, body in enumerate(step_rec.bodies):
                try:
                    rendered_sub = render_template_string(subj, ctx).strip()
                    rendered_body = render_template_string(body, ctx).strip()
                    full_body = f"{rendered_body}\n{sig_block}"
                    lint_res = lint_email(rendered_sub, full_body, step=step_rec.step)
                    if not lint_res.passed:
                        linter_failures.append(
                            f"Step {step_rec.step} v{s_idx}.{b_idx}: {', '.join(lint_res.failures)}"
                        )
                except Exception as e:
                    linter_failures.append(f"Step {step_rec.step} v{s_idx}.{b_idx}: {e}")

    if not linter_failures:
        items.append(
            CheckItem(
                name="Sequence Templates Linter",
                status="PASS",
                message="All sequence step variants passed spam linter rules cleanly.",
            )
        )
    else:
        items.append(
            CheckItem(
                name="Sequence Templates Linter",
                status="FAIL",
                message=f"Linter detected issues in templates: {'; '.join(linter_failures[:3])}",
            )
        )

    # 9. Suppression list loaded
    suppression_count = db.scalar(select(func.count(Suppression.id))) or 0
    if suppression_count > 0:
        items.append(
            CheckItem(
                name="Suppression List",
                status="PASS",
                message=f"Suppression list contains {suppression_count} protected pattern(s).",
            )
        )
    else:
        items.append(
            CheckItem(
                name="Suppression List",
                status="WARN",
                message=(
                    "Suppression list is empty. Consider importing an initial suppression file."
                ),
            )
        )

    # 10. Unapproved leads count
    unapproved_count = (
        db.scalar(
            select(func.count(Lead.id)).where(
                Lead.status.in_([LeadStatus.NEW.value, LeadStatus.VALID.value])
            )
        )
        or 0
    )
    approved_count = (
        db.scalar(select(func.count(Lead.id)).where(Lead.status == LeadStatus.APPROVED.value)) or 0
    )
    items.append(
        CheckItem(
            name="Approval Queue Status",
            status="PASS",
            message=(
                f"{unapproved_count} lead(s) awaiting review, {approved_count} approved and ready."
            ),
        )
    )

    # 11. Live connection check (only if LIVE_MODE=true and live_check=True)
    if live_check:
        if not settings.LIVE_MODE:
            items.append(
                CheckItem(
                    name="Live Server Credentials",
                    status="WARN",
                    message="Skipping live SMTP/IMAP checks because LIVE_MODE is false.",
                )
            )
        else:
            # SMTP Login test
            try:
                smtp = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=10)
                smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.noop()
                smtp.quit()
                items.append(
                    CheckItem(
                        name="Live SMTP Connection",
                        status="PASS",
                        message=(
                            f"Connected and authenticated with "
                            f"{settings.SMTP_HOST}:{settings.SMTP_PORT}."
                        ),
                    )
                )
            except Exception as e:
                items.append(
                    CheckItem(
                        name="Live SMTP Connection",
                        status="FAIL",
                        message=f"SMTP authentication failure: {e}",
                    )
                )

            # IMAP Login test (read-only)
            try:
                imap = imaplib.IMAP4_SSL(settings.IMAP_HOST, settings.IMAP_PORT)
                imap.login(settings.imap_username, settings.imap_password)
                imap.select("INBOX", readonly=True)
                imap.logout()
                items.append(
                    CheckItem(
                        name="Live IMAP Connection",
                        status="PASS",
                        message=(
                            f"Connected and authenticated read-only with "
                            f"{settings.IMAP_HOST}:{settings.IMAP_PORT}."
                        ),
                    )
                )
            except Exception as e:
                items.append(
                    CheckItem(
                        name="Live IMAP Connection",
                        status="FAIL",
                        message=f"IMAP authentication failure: {e}",
                    )
                )

    return DoctorReport(items=items)


def create_sqlite_backup(
    db_path: Path,
    backup_dir: Path,
    max_keep: int = 14,
    clock_dt: date | None = None,
) -> Path:
    """Create a hot online SQLite backup and prune backups older than max_keep."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"backup_{now_str}.db"
    dest_path = backup_dir / backup_filename

    # Perform online SQLite backup
    src_conn = sqlite3.connect(str(db_path))
    try:
        dst_conn = sqlite3.connect(str(dest_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
    finally:
        src_conn.close()

    # Prune backups keeping the last max_keep
    all_backups = sorted(
        backup_dir.glob("backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if len(all_backups) > max_keep:
        for old_backup in all_backups[max_keep:]:
            try:
                old_backup.unlink()
            except OSError:
                pass

    return dest_path
