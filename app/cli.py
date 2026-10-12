"""Command-Line Interface (CLI) for ColdReach management tasks.

Usage:
    python -m app.cli import leads.csv --campaign "SaaS Outreach"
"""

import argparse
import sys
from pathlib import Path

from app.core.db import SessionLocal, init_db
from app.core.logging import setup_logging
from app.services.importer import import_leads


def build_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="coldreach-cli",
        description="ColdReach automation CLI",
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

    return parser


def handle_import(csv_path_str: str, campaign_name: str) -> int:
    """Execute CSV lead import and render user report.

    Args:
        csv_path_str: Relative or absolute path to CSV file.
        campaign_name: Name of target campaign.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
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


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    setup_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "import":
        return handle_import(args.file, args.campaign)

    return 0


if __name__ == "__main__":
    sys.exit(main())
