"""Comprehensive tests for lead CSV importer service."""

import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models import Event, EventType, Lead, Suppression
from app.services.importer import (
    import_leads,
    infer_timezone,
    normalize_name,
    normalize_website,
)

CLEAN_CSV = (
    "email,first_name,last_name,company,website,title,country,timezone,personal_note\n"
    "alice@acme.com,alice,smith,Acme Corp,acme.com,Founder,US,,Loved your podcast\n"
    "bob@tech.co.uk,BOB,JONES,Tech UK,http://tech.co.uk/,CTO,UK,Europe/London,Great talk\n"
    "charlie@dubai.ae,charlie,khan,Gulf Ventures,https://gulf.ae,Partner,UAE,,See you at Gitex\n"
    "dawood@pakistan.pk,dawood,ali,Dev Studio,devstudio.pk/,Lead Architect,Pakistan,,Nice repo\n"
    "eva@berlin.de,EVA,WEISS,Berlin AI,berlin-ai.de,CEO,Germany,,Impressive blog\n"
)

MESSY_CSV = (
    "  E-MAIL  ,  First Name  ,  Last Name  ,  Company Name  ,  URL  ,  Job Title  ,  Country\n"
    "alice@acme.com,alice,smith,Acme Corp,acme.com,Founder,US\n"
    "ALICE@ACME.COM,Alice,Smith,Acme Corp,acme.com,Founder,US\n"
    "bad-email-without-at,Bad,User,Spam Inc,spam.com,Tester,US\n"
    "missing-domain@,No,Domain,Broken Co,broken.com,Dev,US\n"
    ",Ghost,User,Ghost LLC,ghost.com,Spectre,US\n"
    "suppressed@victim.com,Sam,Suppressed,Block Co,block.com,Manager,US\n"
    "   rene@montreal.ca   , René , Lévesque , MTL Tech , http://mtl.ca/ , VP , Canada\n"
    "muller@munich.de, Jürgen , Müller , Munich Auto , munich.de , Director , Germany\n"
    "\u200bextra_junk@example.com\u200b, Zack , Taylor , Clean Co , cleanco.com , Dev , Australia\n"
    "   ,   ,   ,   ,   ,   ,   \n"
)


@pytest.fixture
def test_db() -> Session:
    """Provide a fresh in-memory database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_normalization_helpers() -> None:
    """Test text and URL normalization helpers."""
    assert normalize_website("example.com") == "https://example.com"
    assert normalize_website("http://example.com/") == "https://example.com"
    assert normalize_website("https://example.com/sub") == "https://example.com/sub"
    assert normalize_website("   ") is None
    assert normalize_website(None) is None

    assert normalize_name("john doe") == "John Doe"
    assert normalize_name("  ALICE  SMITH  ") == "Alice Smith"
    assert normalize_name("jean-luc") == "Jean-luc"

    assert infer_timezone("US", None) == "America/New_York"
    assert infer_timezone("Pakistan", None) == "Asia/Karachi"
    assert infer_timezone("UAE", None) == "Asia/Dubai"
    assert infer_timezone("Germany", None) == "Europe/Berlin"
    assert infer_timezone("Australia", None) == "Australia/Sydney"
    assert infer_timezone("Unknown Land", None) is None
    # Explicit timezone overrides inference
    assert infer_timezone("US", "America/Chicago") == "America/Chicago"


def test_clean_csv_import(test_db: Session, fake_clock: FakeClock, tmp_path: Path) -> None:
    """Verify that clean CSV imports expected fields, timezone, and audit events."""
    report = import_leads(
        db=test_db,
        campaign_name="Clean Campaign",
        csv_file=CLEAN_CSV,
        clock=fake_clock,
        data_dir=tmp_path,
    )

    assert report.total_rows == 5
    assert report.imported == 5
    assert report.duplicates == 0
    assert report.suppressed == 0
    assert report.rejected == 0
    assert report.reject_items == []

    leads = test_db.scalars(select(Lead)).all()
    assert len(leads) == 5

    lead_map = {ld.email: ld for ld in leads}
    assert "alice@acme.com" in lead_map
    alice = lead_map["alice@acme.com"]
    assert alice.first_name == "Alice"
    assert alice.website == "https://acme.com"
    assert alice.timezone == "America/New_York"

    bob = lead_map["bob@tech.co.uk"]
    assert bob.first_name == "Bob"
    assert bob.website == "https://tech.co.uk"
    assert bob.timezone == "Europe/London"

    dawood = lead_map["dawood@pakistan.pk"]
    assert dawood.timezone == "Asia/Karachi"

    events = test_db.scalars(select(Event).where(Event.type == EventType.IMPORTED.value)).all()
    assert len(events) == 5


def test_messy_csv_import(test_db: Session, fake_clock: FakeClock, tmp_path: Path) -> None:
    """Verify handling of messy CSV: rejections, deduplication, suppression, and unicode."""
    test_db.add(Suppression(email="suppressed@victim.com", reason="requested_removal"))
    test_db.commit()

    report = import_leads(
        db=test_db,
        campaign_name="Messy Campaign",
        csv_file=MESSY_CSV,
        clock=fake_clock,
        data_dir=tmp_path,
    )

    assert report.total_rows == 9
    assert report.imported == 4
    assert report.duplicates == 1
    assert report.suppressed == 1
    assert report.rejected == 3

    reasons = [r.reason for r in report.reject_items]
    assert "Malformed email format" in reasons
    assert "Missing email address" in reasons

    assert report.rejects_file_path is not None
    assert Path(report.rejects_file_path).exists()
    reject_content = Path(report.rejects_file_path).read_text(encoding="utf-8")
    assert "Missing email address" in reject_content
    assert "bad-email-without-at" in reject_content

    leads = {ld.email: ld for ld in test_db.scalars(select(Lead)).all()}
    rene = leads["rene@montreal.ca"]
    assert rene.first_name == "René"
    assert rene.timezone == "America/Toronto"

    muller = leads["muller@munich.de"]
    assert muller.first_name == "Jürgen"
    assert muller.last_name == "Müller"

    extra = leads["extra_junk@example.com"]
    assert extra.email == "extra_junk@example.com"
    assert extra.timezone == "Australia/Sydney"


def test_import_idempotency(test_db: Session, fake_clock: FakeClock, tmp_path: Path) -> None:
    """Verify that importing the exact same file twice creates 0 new leads."""
    report_1 = import_leads(
        db=test_db,
        campaign_name="Idempotent Campaign",
        csv_file=CLEAN_CSV,
        clock=fake_clock,
        data_dir=tmp_path,
    )
    assert report_1.imported == 5
    assert report_1.duplicates == 0

    report_2 = import_leads(
        db=test_db,
        campaign_name="Idempotent Campaign",
        csv_file=CLEAN_CSV,
        clock=fake_clock,
        data_dir=tmp_path,
    )
    assert report_2.imported == 0
    assert report_2.duplicates == 5
    assert report_2.total_rows == 5

    total_leads = test_db.scalars(select(Lead)).all()
    assert len(total_leads) == 5


def test_missing_optional_columns_does_not_crash(test_db: Session, fake_clock: FakeClock) -> None:
    """Verify importing a minimal CSV missing optional columns does not crash."""
    minimal_csv = "email,first_name\ndev1@acme.org,Dave\ndev2@acme.org,Dana\n"
    report = import_leads(
        db=test_db,
        campaign_name="Minimal Campaign",
        csv_file=minimal_csv,
        clock=fake_clock,
    )
    assert report.imported == 2
    assert report.total_rows == 2

    lead = test_db.scalar(select(Lead).where(Lead.email == "dev1@acme.org"))
    assert lead is not None
    assert lead.first_name == "Dave"
    assert lead.company is None
    assert lead.website is None
    assert lead.timezone is None


def test_2000_row_file_performance(test_db: Session, fake_clock: FakeClock, tmp_path: Path) -> None:
    """Verify that a 2,000-row file imports in a few seconds."""
    csv_lines = ["email,first_name,last_name,company,website,country"]
    for i in range(1, 2001):
        country = "US" if i % 2 == 0 else "Pakistan"
        csv_lines.append(f"user{i}@co{i}.com,U{i},T{i},Co {i},co{i}.com,{country}")
    large_csv = "\n".join(csv_lines)

    start_time = time.perf_counter()
    report = import_leads(
        db=test_db,
        campaign_name="Scale Campaign",
        csv_file=large_csv,
        clock=fake_clock,
        data_dir=tmp_path,
    )
    duration = time.perf_counter() - start_time

    assert report.total_rows == 2000
    assert report.imported == 2000
    assert report.duplicates == 0
    assert report.rejected == 0
    assert duration < 5.0, f"Import took too long: {duration:.2f} seconds"
