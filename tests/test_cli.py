"""Tests for CLI lead import, validate, suppress, and preview commands."""

from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.cli import handle_import, main
from app.core.db import SessionLocal, init_db
from app.models import Campaign, Lead, LeadStatus


def test_cli_import_file_not_found() -> None:
    """Verify that importing a non-existent file returns exit code 1."""
    code = handle_import("non_existent_file.csv", "Test Campaign")
    assert code == 1


def test_cli_import_success(tmp_path: Path) -> None:
    """Verify that CLI successfully imports a CSV and returns exit code 0."""
    csv_file = tmp_path / "leads.csv"
    csv_file.write_text(
        "email,first_name,company\njane@example.com,Jane,Example Co\n",
        encoding="utf-8",
    )

    with patch("sys.stdout"):
        code = handle_import(str(csv_file), "CLI Campaign")
        assert code == 0


def test_cli_validate_command() -> None:
    """Verify that CLI validate executes and exits with 0."""
    with patch("sys.stdout"):
        code = main(["validate", "--campaign", "NonExistentCampaign"])
        assert code == 0


def test_cli_suppress_lifecycle() -> None:
    """Verify CLI suppress add, check, and remove operations."""
    with patch("sys.stdout"):
        # Add suppression
        code_add = main(["suppress", "add", "cli_user@block.com", "--reason", "manual"])
        assert code_add == 0

        # Check suppression (exits 0 if suppressed)
        code_check = main(["suppress", "check", "cli_user@block.com"])
        assert code_check == 0

        # Remove suppression
        code_remove = main(["suppress", "remove", "cli_user@block.com"])
        assert code_remove == 0


def test_cli_preview_command() -> None:
    """Verify that CLI preview executes cleanly for a lead."""
    init_db()
    with SessionLocal() as db:
        campaign = db.scalar(select(Campaign).where(Campaign.name == "CLI Preview Campaign"))
        if not campaign:
            campaign = Campaign(name="CLI Preview Campaign", niche="AI")
            db.add(campaign)
            db.commit()

        lead = db.scalar(select(Lead).where(Lead.email == "cli_preview@test.com"))
        if not lead:
            lead = Lead(
                campaign_id=campaign.id,
                email="cli_preview@test.com",
                first_name="Paul",
                company="Test Inc",
                personal_note="Loved your talk on agentic coding at the recent conference.",
                status=LeadStatus.VALID.value,
            )
            db.add(lead)
            db.commit()
        lead_id = lead.id

    with patch("sys.stdout"):
        code = main(
            [
                "preview",
                "--campaign",
                "CLI Preview Campaign",
                "--lead-id",
                str(lead_id),
                "--step",
                "1",
            ]
        )
        assert code == 0
