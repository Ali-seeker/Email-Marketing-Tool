"""Command-Line Interface (CLI) for ColdReach management tasks.

Usage:
    python -m app.cli import leads.csv --campaign "SaaS Outreach"
    python -m app.cli validate --campaign "SaaS Outreach"
    python -m app.cli suppress add someone@x.com --reason manual
    python -m app.cli preview --campaign "SaaS Outreach" --lead-id 5 --step 1
"""

import argparse
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.db import SessionLocal, init_db
from app.core.logging import setup_logging
from app.models.campaign import Campaign
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead
from app.services.importer import import_leads
from app.services.planner import get_mailbox_tz, plan_day
from app.services.suppression import add as add_suppression
from app.services.suppression import is_suppressed
from app.services.suppression import remove as remove_suppression
from app.services.templates import render_step_email
from app.services.validator import validate_leads


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="coldreach-cli",
        description="ColdReach outreach automation CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Subcommand: import
    import_parser = subparsers.add_parser("import", help="Import leads from CSV file")
    import_parser.add_argument("file", type=str, help="Path to input CSV file")
    import_parser.add_argument(
        "--campaign",
        "-c",
        type=str,
        required=True,
        help="Name of target outreach campaign",
    )

    # Subcommand: validate
    validate_parser = subparsers.add_parser("validate", help="Validate new leads in a campaign")
    validate_parser.add_argument(
        "--campaign",
        "-c",
        type=str,
        default=None,
        help="Name of target outreach campaign (validates all if omitted)",
    )
    validate_parser.add_argument(
        "--allow-risky",
        action="store_true",
        default=None,
        help="Permit risky leads to be marked valid instead of invalid",
    )

    # Subcommand: preview
    preview_parser = subparsers.add_parser(
        "preview", help="Preview rendered email step and spam linter result"
    )
    preview_parser.add_argument(
        "--campaign",
        "-c",
        type=str,
        required=True,
        help="Target campaign name",
    )
    preview_parser.add_argument(
        "--lead-id",
        "-l",
        type=int,
        required=True,
        help="ID of target lead to preview",
    )
    preview_parser.add_argument(
        "--step",
        "-s",
        type=int,
        default=1,
        help="Sequence step index (1, 2, 3, 4)",
    )

    # Subcommand: suppress
    suppress_parser = subparsers.add_parser("suppress", help="Manage suppression list")
    suppress_subparsers = suppress_parser.add_subparsers(dest="suppress_action", required=True)

    # suppress add
    add_parser = suppress_subparsers.add_parser("add", help="Add an email or domain to suppression")
    add_parser.add_argument(
        "target", type=str, help="Email address or domain (e.g. competitor.com)"
    )
    add_parser.add_argument(
        "--reason",
        "-r",
        type=str,
        default="manual",
        help="Reason for suppression (default: manual)",
    )
    add_parser.add_argument(
        "--domain",
        action="store_true",
        help="Force target to be treated as a domain block",
    )

    # suppress remove
    rem_parser = suppress_subparsers.add_parser("remove", help="Remove target from suppression")
    rem_parser.add_argument("target", type=str, help="Email address or domain to remove")

    # suppress check
    check_parser = suppress_subparsers.add_parser("check", help="Check if target is suppressed")
    check_parser.add_argument("email", type=str, help="Email address to check")

    # Subcommand: plan
    plan_parser = subparsers.add_parser(
        "plan", help="Plan outbound email dispatch schedule for a specific date"
    )
    plan_parser.add_argument(
        "--date",
        "-d",
        type=str,
        default=None,
        help="Target date for planning (YYYY-MM-DD)",
    )

    return parser


def handle_import(csv_path_str: str, campaign_name: str) -> int:
    """Execute CSV lead import and render user report."""
    file_path = Path(csv_path_str)
    if not file_path.is_file():
        print(f"Error: File not found: {csv_path_str}", file=sys.stderr)
        return 1

    init_db()
    with SessionLocal() as db:
        report = import_leads(db=db, campaign_name=campaign_name, csv_file=file_path)

    print("\n" + "=" * 50)
    print("           COLDREACH IMPORT REPORT")
    print("=" * 50)
    print(f" Campaign      : {report.campaign_name} (ID: {report.campaign_id})")
    print(f" Total Rows    : {report.total_rows}")
    print(f" Imported      : {report.imported}")
    print(f" Duplicates    : {report.duplicates}")
    print(f" Suppressed    : {report.suppressed}")
    print(f" Rejected      : {report.rejected}")

    if report.reject_items:
        print("\nRejection Breakdown:")
        for item in report.reject_items:
            print(f"  - Row {item.row_number}: {item.reason} ({item.email or 'N/A'})")

    if report.rejects_file_path:
        print(f"\nRejects saved to: {report.rejects_file_path}")

    print("=" * 50 + "\n")
    return 0


def handle_validate(campaign_name: str | None, allow_risky: bool | None) -> int:
    """Execute lead validation on unverified leads."""
    init_db()
    with SessionLocal() as db:
        summary = validate_leads(
            db=db,
            campaign_name=campaign_name,
            allow_risky=allow_risky,
        )

    print("\n" + "=" * 50)
    print("          COLDREACH VALIDATION REPORT")
    print("=" * 50)
    print(f" Target Campaign : {campaign_name or 'ALL CAMPAIGNS'}")
    print(f" Total Evaluated : {summary.total_checked}")
    print(f" Marked Valid    : {summary.valid_count}")
    print(f" Marked Invalid  : {summary.invalid_count}")
    print(" Risk Breakdown  :")
    print(f"   - OK          : {summary.risk_ok}")
    print(f"   - Risky       : {summary.risk_risky}")
    print(f"   - Bad         : {summary.risk_bad}")

    if summary.details:
        print("\nClassification Details (Sample):")
        for item in summary.details[:15]:
            status_tag = item["status"].upper()
            print(f"  [{status_tag}] {item['email']} ({item['risk_level']}): {item['reason']}")

    print("=" * 50 + "\n")
    return 0


def handle_preview(campaign_name: str, lead_id: int, step: int) -> int:
    """Preview rendered email and spam linter analysis for a lead."""
    init_db()
    with SessionLocal() as db:
        campaign = db.scalar(select(Campaign).where(Campaign.name == campaign_name))
        if not campaign:
            print(f"Error: Campaign '{campaign_name}' not found", file=sys.stderr)
            return 1

        lead = db.get(Lead, lead_id)
        if not lead or lead.campaign_id != campaign.id:
            print(
                f"Error: Lead #{lead_id} not found in campaign '{campaign_name}'",
                file=sys.stderr,
            )
            return 1

        rendered = render_step_email(lead=lead, step_num=step, db=db)

    print("\n" + "=" * 60)
    print(f"     EMAIL PREVIEW: {campaign_name} | Step {step}")
    print(f"     Target Lead: {lead.first_name} <{lead.email}> (ID: {lead.id})")
    print("=" * 60)
    print(f"Subject Variant Index: {rendered.subject_variant_index}")
    print(f"Body Variant Index   : {rendered.body_variant_index}")
    print(f"Send as Reply        : {rendered.send_as_reply}")
    print(f"Ready Status         : {rendered.status.upper()} (is_ready={rendered.is_ready})")
    if rendered.error:
        print(f"Status Note          : {rendered.error}")

    print("-" * 60)
    print(f"SUBJECT: {rendered.subject}")
    print("-" * 60)
    print(rendered.body)
    print("-" * 60)
    print(f"SPAM LINTER VERDICT: {'PASSED' if rendered.lint_result.passed else 'FAILED'}")
    if rendered.lint_result.failures:
        print("Failures:")
        for fail in rendered.lint_result.failures:
            print(f"  [X] {fail}")
    if rendered.lint_result.warnings:
        print("Warnings:")
        for warn in rendered.lint_result.warnings:
            print(f"  [!] {warn}")
    print("=" * 60 + "\n")
    return 0


def handle_suppress(args: argparse.Namespace) -> int:
    """Handle suppression subcommands."""
    init_db()
    with SessionLocal() as db:
        if args.suppress_action == "add":
            record = add_suppression(
                db=db,
                target=args.target,
                reason=args.reason,
                is_domain=args.domain if args.domain else None,
            )
            print(
                f"\nSuppressed: {record.email} (is_domain={record.is_domain}, "
                f"reason={record.reason})"
            )
            return 0
        elif args.suppress_action == "remove":
            removed = remove_suppression(db=db, target=args.target)
            if removed:
                print(f"\nRemoved suppression for: {args.target}")
                return 0
            else:
                print(f"\nTarget not found in suppression list: {args.target}", file=sys.stderr)
                return 1
        elif args.suppress_action == "check":
            suppressed = is_suppressed(db=db, email=args.email)
            print(f"\n{args.email} is {'SUPPRESSED' if suppressed else 'NOT suppressed'}")
            return 0 if suppressed else 1

    return 0


def handle_plan(date_str: str | None) -> int:
    """Plan outbound emails for a date and print scheduling table."""
    init_db()
    with SessionLocal() as db:
        if date_str:
            target_date = date.fromisoformat(date_str.strip())
        else:
            mailbox_tz = get_mailbox_tz(db)
            target_date = datetime.now(mailbox_tz).date()

        # Execute daily send planner
        planned_msgs = plan_day(target_date=target_date, db=db)

        # Retrieve scheduled messages for target date in mailbox timezone
        mailbox_tz = get_mailbox_tz(db)
        stmt = (
            select(EmailMessage)
            .where(EmailMessage.status == EmailMessageStatus.SCHEDULED.value)
            .order_by(EmailMessage.scheduled_at.asc())
        )
        all_scheduled = db.scalars(stmt).all()
        day_msgs = [
            m
            for m in all_scheduled
            if m.scheduled_at is not None
            and m.scheduled_at.astimezone(mailbox_tz).date() == target_date
        ]

        display_msgs = day_msgs if day_msgs else planned_msgs

        print("\n" + "=" * 115)
        print(f"                                COLDREACH SEND PLAN: {target_date.isoformat()}")
        print("=" * 105)
        if not display_msgs:
            print(" No emails scheduled for this date (zero capacity or no due emails).")
            print("=" * 105 + "\n")
            return 0

        header = (
            f"{'Lead':<25} | {'Email':<28} | {'Step':<4} | "
            f"{'Local Time (Lead TZ)':<23} | {'UTC':<20}"
        )
        print(header)
        print("-" * 105)
        for msg in display_msgs:
            lead = msg.lead or db.get(Lead, msg.lead_id)
            lead_name = (
                f"{lead.first_name or ''} {lead.last_name or ''}".strip()
                if lead
                else f"Lead #{msg.lead_id}"
            )
            if not lead_name:
                lead_name = f"Lead #{msg.lead_id}"
            email_addr = lead.email if lead else "N/A"

            lead_tz = mailbox_tz
            if lead and lead.timezone:
                try:
                    lead_tz = ZoneInfo(lead.timezone)
                except Exception:
                    pass

            if msg.scheduled_at:
                sched_utc = (
                    msg.scheduled_at
                    if msg.scheduled_at.tzinfo
                    else msg.scheduled_at.replace(tzinfo=UTC)
                )
                sched_local = sched_utc.astimezone(lead_tz)
                local_str = sched_local.strftime("%Y-%m-%d %H:%M %Z")
                utc_str = sched_utc.strftime("%Y-%m-%d %H:%M UTC")
            else:
                local_str = "Not scheduled"
                utc_str = "N/A"

            row = (
                f"{lead_name[:25]:<25} | {email_addr[:28]:<28} | {msg.step:<4} | "
                f"{local_str:<23} | {utc_str:<20}"
            )
            print(row)

        print("=" * 105)
        print(f" Total scheduled: {len(display_msgs)} email(s)\n")
        return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "import":
        return handle_import(args.file, args.campaign)
    elif args.command == "validate":
        return handle_validate(args.campaign, args.allow_risky)
    elif args.command == "preview":
        return handle_preview(args.campaign, args.lead_id, args.step)
    elif args.command == "suppress":
        return handle_suppress(args)
    elif args.command == "plan":
        return handle_plan(args.date)

    return 0


if __name__ == "__main__":
    sys.exit(main())
