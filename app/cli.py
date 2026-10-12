"""Command-Line Interface (CLI) for ColdReach management tasks.

Usage:
    python -m app.cli import leads.csv --campaign "SaaS Outreach"
    python -m app.cli validate --campaign "SaaS Outreach"
    python -m app.cli suppress add someone@x.com --reason manual
"""

import argparse
import sys
from pathlib import Path

from app.core.db import SessionLocal, init_db
from app.core.logging import setup_logging
from app.services.importer import import_leads
from app.services.suppression import add as add_suppression
from app.services.suppression import is_suppressed
from app.services.suppression import remove as remove_suppression
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


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "import":
        return handle_import(args.file, args.campaign)
    elif args.command == "validate":
        return handle_validate(args.campaign, args.allow_risky)
    elif args.command == "suppress":
        return handle_suppress(args)

    return 0


if __name__ == "__main__":
    sys.exit(main())
