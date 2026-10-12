"""Tests for CLI lead import command."""

from pathlib import Path
from unittest.mock import patch

from app.cli import handle_import, main


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


def test_cli_main_dispatch(tmp_path: Path) -> None:
    """Verify main argument parsing and handler execution."""
    csv_file = tmp_path / "cli_leads.csv"
    csv_file.write_text(
        "email,first_name\ntest@cli.com,Tester\n",
        encoding="utf-8",
    )

    with patch("sys.stdout"):
        code = main(["import", str(csv_file), "--campaign", "CLI Main Campaign"])
        assert code == 0
