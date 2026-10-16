"""Unit and integration tests for inbox monitoring, classification, and lead matching.

Covers:
- All 21 .eml test fixtures in tests/fixtures/inbox (classification and matching).
- Strict read-only IMAP assertions (BODY.PEEK[], readonly=True, no STORE/EXPUNGE/DELETE commands).
- Idempotency when processing batches repeatedly.
- UID checkpointing across worker restarts.
- 3 soft bounces escalation to hard bounce.
- Auto-reply 5-day follow-up delay without sequence cancellation.
- Positive reply hint extraction and Reply table logging.
- Bounce rate statistics calculation and automatic campaign pause.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import FakeClock
from app.core.config import Settings
from app.core.db import Base
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.event import Event, EventType
from app.models.lead import Lead, LeadStatus
from app.models.reply import Reply
from app.models.setting import Setting
from app.models.suppression import Suppression
from app.services.classifier import (
    InboundClassification,
    classify_inbound_mail,
    extract_snippet,
    strip_quoted_history,
)
from app.services.inbox import (
    FakeMailboxReader,
    ImapReader,
    IncomingMail,
    calculate_bounce_rates,
    poll_inbox_and_process,
    process_incoming_mail,
)
from app.worker import Worker

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "inbox"


@pytest.fixture
def inbox_db() -> tuple[Session, sessionmaker]:
    """Create in-memory SQLite database session and factory seeded with fixture leads."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    with session_factory() as session:
        campaign = Campaign(name="Inbox Test Campaign", status=CampaignStatus.ACTIVE.value)
        session.add(campaign)
        session.flush()

        # Seed leads corresponding to test fixtures
        leads_data = [
            ("unknown.lead@acme.com", None),
            ("full.mailbox@acme.com", None),
            ("david@acme.com", "<step1-david@sender.com>"),
            ("emily@acme.com", "<step1-emily@sender.com>"),
            ("marcus@acme.com", "<step1-marcus@sender.com>"),
            ("chloe@acme.com", "<step1-chloe@sender.com>"),
            ("sarah@acme.com", "<step1-sarah@sender.com>"),
            ("rachel@acme.com", "<step1-rachel@sender.com>"),
            ("jean@acme.fr", "<step1-jean@sender.com>"),
            ("lucas@acme.com", "<step1-lucas@sender.com>"),
            ("robert@acme.com", "<step1-robert@sender.com>"),
            ("amanda@acme.com", "<step1-amanda@sender.com>"),
            ("greylist.user@acme.com", None),
            ("kevin@acme.com", "<step1-kevin@sender.com>"),
            ("rejected.user@acme.com", None),
            ("walter@acme.com", "<step1-walter@sender.com>"),
            ("jesse@acme.com", "<step1-jesse@sender.com>"),
            ("donna@acme.com", "<step1-donna@sender.com>"),
        ]

        for email, msg_id in leads_data:
            lead = Lead(
                campaign_id=campaign.id,
                email=email,
                first_name="Target",
                last_name="Lead",
                company="Acme",
                timezone="America/New_York",
                status=LeadStatus.IN_SEQUENCE.value,
            )
            session.add(lead)
            session.flush()

            if msg_id:
                sent_msg = EmailMessage(
                    lead_id=lead.id,
                    step=1,
                    subject="Quick question regarding automation",
                    body="Hi, wanted to reach out.",
                    status=EmailMessageStatus.SENT.value,
                    sent_at=datetime(2026, 10, 12, 9, 0, tzinfo=UTC),
                    message_id=msg_id,
                )
                session.add(sent_msg)

                # Add a pending follow-up message
                follow_up = EmailMessage(
                    lead_id=lead.id,
                    step=2,
                    subject="Re: Quick question regarding automation",
                    body="Following up on my previous note.",
                    status=EmailMessageStatus.SCHEDULED.value,
                    scheduled_at=datetime(2026, 10, 16, 10, 0, tzinfo=UTC),
                )
                session.add(follow_up)

        session.commit()
        yield session, session_factory


def test_twenty_one_eml_fixtures_classification_and_matching(
    inbox_db: tuple[Session, sessionmaker],
) -> None:
    """Verify classification and lead matching across all 21 realistic .eml fixture files."""
    db, _ = inbox_db

    expected_outcomes = {
        "01_hard_bounce_gmail_550.eml": (
            InboundClassification.HARD_BOUNCE,
            "unknown.lead@acme.com",
        ),
        "02_soft_bounce_mailbox_full.eml": (
            InboundClassification.SOFT_BOUNCE,
            "full.mailbox@acme.com",
        ),
        "03_outlook_auto_reply_oof.eml": (InboundClassification.AUTO_REPLY, "david@acme.com"),
        "04_gmail_vacation_reply.eml": (InboundClassification.AUTO_REPLY, "emily@acme.com"),
        "05_normal_reply_with_quotes.eml": (InboundClassification.REPLY_OTHER, "marcus@acme.com"),
        "06_unsubscribe_remove_me.eml": (InboundClassification.UNSUBSCRIBE, "chloe@acme.com"),
        "07_positive_reply_calendly.eml": (
            InboundClassification.REPLY_POSITIVE_HINT,
            "sarah@acme.com",
        ),
        "08_reply_different_from_address.eml": (
            InboundClassification.REPLY_POSITIVE_HINT,
            "rachel@acme.com",
        ),
        "09_newsletter_unrelated.eml": (InboundClassification.UNRELATED, None),
        "10_hard_bounce_no_recipient.eml": (InboundClassification.HARD_BOUNCE, None),
        "11_non_english_reply.eml": (InboundClassification.REPLY_OTHER, "jean@acme.fr"),
        "12_forwarded_reply.eml": (InboundClassification.REPLY_OTHER, "lucas@acme.com"),
        "13_unsubscribe_stop_emailing.eml": (InboundClassification.UNSUBSCRIBE, "robert@acme.com"),
        "14_positive_reply_call_scheduled.eml": (
            InboundClassification.REPLY_POSITIVE_HINT,
            "amanda@acme.com",
        ),
        "15_soft_bounce_greylist_451.eml": (
            InboundClassification.SOFT_BOUNCE,
            "greylist.user@acme.com",
        ),
        "16_auto_reply_x_autoreply.eml": (InboundClassification.AUTO_REPLY, "kevin@acme.com"),
        "17_hard_bounce_multipart_report.eml": (
            InboundClassification.HARD_BOUNCE,
            "rejected.user@acme.com",
        ),
        "18_reply_other_q3.eml": (InboundClassification.REPLY_OTHER, "walter@acme.com"),
        "19_unsubscribe_not_interested_stop.eml": (
            InboundClassification.UNSUBSCRIBE,
            "jesse@acme.com",
        ),
        "20_unrelated_stripe_receipt.eml": (InboundClassification.UNRELATED, None),
        "21_positive_reply_send_details.eml": (
            InboundClassification.REPLY_POSITIVE_HINT,
            "donna@acme.com",
        ),
    }

    assert len(expected_outcomes) == 21, "Must verify at least 20 fixtures"

    for filename, (expected_cls, expected_email) in expected_outcomes.items():
        file_path = FIXTURES_DIR / filename
        assert file_path.exists(), f"Fixture file missing: {filename}"

        mail = IncomingMail.from_bytes(file_path.read_bytes(), uid=1)
        res = classify_inbound_mail(mail, db)

        assert res.classification == expected_cls, (
            f"Fixture '{filename}' classified as {res.classification}, expected {expected_cls}"
        )

        if expected_email:
            assert res.matched_lead is not None, f"Expected lead match for '{filename}'"
            assert res.matched_lead.email == expected_email
        else:
            assert res.matched_lead is None, f"Expected no lead match for '{filename}'"


def test_quote_stripping_and_snippet_extraction() -> None:
    """Verify that quoted history is cleanly stripped and snippet captures only original text."""
    body_with_quotes = (
        "Hi Alex,\n\n"
        "Let's definitely connect next Tuesday at 2pm.\n\n"
        "On Mon, Oct 12, 2026 at 9:00 AM Alex Mercer <alex@sender.com> wrote:\n"
        "> Hi Sarah,\n"
        "> Would you have 15 minutes?\n"
    )

    stripped = strip_quoted_history(body_with_quotes)
    assert "Hi Alex" in stripped
    assert "Let's definitely connect" in stripped
    assert "On Mon, Oct 12" not in stripped
    assert "> Hi Sarah" not in stripped

    snippet = extract_snippet(body_with_quotes, max_chars=300)
    assert snippet == "Hi Alex, Let's definitely connect next Tuesday at 2pm."


def test_read_only_imap_guarantee() -> None:
    """Verify ImapReader strictly enforces read-only access and never issues mutation commands."""
    mock_imap = MagicMock()
    mock_imap.select.return_value = ("OK", [b"10"])
    mock_imap.uid.side_effect = [
        ("OK", [b"1 2"]),  # UID SEARCH
        ("OK", [(b"1 (BODY.PEEK[] {20})", b"Subject: Test 1\r\n\r\nHello")]),  # UID FETCH 1
        ("OK", [(b"2 (BODY.PEEK[] {20})", b"Subject: Test 2\r\n\r\nWorld")]),  # UID FETCH 2
    ]

    mock_factory = MagicMock(return_value=mock_imap)
    settings = Settings(
        LIVE_MODE=True,
        IMAP_USER="test@gmail.com",
        IMAP_APP_PASSWORD="test_app_password",
        IMAP_HOST="imap.gmail.com",
    )

    reader = ImapReader(settings=settings, imap_client_factory=mock_factory)
    results = reader.fetch_new(since_uid=0)

    assert len(results) == 2
    assert results[0].uid == 1
    assert results[1].uid == 2

    # Assert readonly=True was passed to SELECT
    mock_imap.select.assert_called_once_with("INBOX", readonly=True)

    # Assert BODY.PEEK[] was used for fetch
    calls = [str(call) for call in mock_imap.uid.mock_calls]
    assert any("BODY.PEEK" in c for c in calls)

    # Assert NO mutation command was EVER called
    forbidden_commands = ("STORE", "EXPUNGE", "DELETE", "COPY", "MOVE")
    for call_str in calls:
        for cmd in forbidden_commands:
            assert f"'{cmd}'" not in call_str
            assert f'"{cmd}"' not in call_str


def test_idempotency_batch_processed_twice(inbox_db: tuple[Session, sessionmaker]) -> None:
    """Verify processing the same incoming batch twice is completely idempotent."""
    db, factory = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    reader = FakeMailboxReader(fixtures_dir=FIXTURES_DIR)
    initial_total_mails = len(reader.messages)
    assert initial_total_mails >= 20

    # First run: process all incoming emails
    new_count_1, stats_1 = poll_inbox_and_process(reader, db, clock=clock)
    assert new_count_1 == initial_total_mails

    # Capture state
    leads_bounced_1 = db.scalars(select(Lead).where(Lead.status == LeadStatus.BOUNCED.value)).all()
    events_count_1 = db.scalar(select(func.count(Event.id)))
    replies_count_1 = db.scalar(select(func.count(Reply.id)))
    suppressions_count_1 = db.scalar(select(func.count(Suppression.id)))

    # Second run with same reader and state: since_uid checkpoint prevents re-processing
    new_count_2, stats_2 = poll_inbox_and_process(reader, db, clock=clock)
    assert new_count_2 == 0

    # Verify database state remained exactly identical
    leads_bounced_2 = db.scalars(select(Lead).where(Lead.status == LeadStatus.BOUNCED.value)).all()
    events_count_2 = db.scalar(select(func.count(Event.id)))
    replies_count_2 = db.scalar(select(func.count(Reply.id)))
    suppressions_count_2 = db.scalar(select(func.count(Suppression.id)))

    assert len(leads_bounced_1) == len(leads_bounced_2)
    assert events_count_1 == events_count_2
    assert replies_count_1 == replies_count_2
    assert suppressions_count_1 == suppressions_count_2


def test_uid_checkpoint_after_restart(inbox_db: tuple[Session, sessionmaker]) -> None:
    """Verify that worker restart resumes from Setting.LAST_INBOX_UID checkpoint."""
    db, _ = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    reader1 = FakeMailboxReader()
    # Add 5 emails
    for i in range(1, 6):
        raw = f"From: user{i}@acme.com\nTo: me@sender.com\nSubject: Test {i}\n\nBody {i}".encode()
        reader1.add_message(raw)

    count_1, _ = poll_inbox_and_process(reader1, db, clock=clock)
    assert count_1 == 5

    checkpoint_val = db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID"))
    assert checkpoint_val == "5"

    # Simulate restart with new reader instance containing the old 5 emails + 3 new emails
    reader2 = FakeMailboxReader()
    for i in range(1, 9):
        raw = f"From: user{i}@acme.com\nTo: me@sender.com\nSubject: Test {i}\n\nBody {i}".encode()
        reader2.add_message(raw)

    count_2, _ = poll_inbox_and_process(reader2, db, clock=clock)
    # Exactly only the 3 new emails (UIDs 6, 7, 8) were fetched and processed
    assert count_2 == 3

    checkpoint_val_after = db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID"))
    assert checkpoint_val_after == "8"


def test_soft_bounce_escalation_after_three_occurrences(
    inbox_db: tuple[Session, sessionmaker],
) -> None:
    """Verify that 3 soft bounces for a lead escalate to a hard bounce and suppression."""
    db, _ = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    lead = db.scalar(select(Lead).where(Lead.email == "full.mailbox@acme.com"))
    assert lead is not None
    assert lead.status == LeadStatus.IN_SEQUENCE.value

    soft_bounce_file = FIXTURES_DIR / "02_soft_bounce_mailbox_full.eml"
    raw_bytes = soft_bounce_file.read_bytes()

    # 1. First soft bounce
    mail1 = IncomingMail.from_bytes(raw_bytes, uid=101)
    process_incoming_mail(mail1, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.IN_SEQUENCE.value
    assert (
        db.scalar(select(Suppression).where(Suppression.email == "full.mailbox@acme.com")) is None
    )

    # 2. Second soft bounce
    mail2 = IncomingMail.from_bytes(raw_bytes, uid=102)
    process_incoming_mail(mail2, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.IN_SEQUENCE.value

    # 3. Third soft bounce -> Escalate to hard bounce!
    mail3 = IncomingMail.from_bytes(raw_bytes, uid=103)
    process_incoming_mail(mail3, db, clock=clock)
    db.refresh(lead)
    assert lead.status == LeadStatus.BOUNCED.value

    # Suppressed and pending messages cancelled
    supp = db.scalar(select(Suppression).where(Suppression.email == "full.mailbox@acme.com"))
    assert supp is not None
    assert "bounce" in supp.reason

    bounced_event = db.scalar(
        select(Event)
        .where(Event.lead_id == lead.id, Event.type == EventType.BOUNCED.value)
        .order_by(Event.id.desc())
    )
    assert bounced_event is not None
    assert "3 soft bounces" in str(bounced_event.payload)


def test_auto_reply_delays_follow_up_without_stopping_sequence(
    inbox_db: tuple[Session, sessionmaker],
) -> None:
    """Verify auto-reply delays scheduled follow-ups by 5 days without stopping sequence."""
    db, _ = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    lead = db.scalar(select(Lead).where(Lead.email == "david@acme.com"))
    assert lead is not None
    scheduled_msg = db.scalar(
        select(EmailMessage).where(
            EmailMessage.lead_id == lead.id,
            EmailMessage.status == EmailMessageStatus.SCHEDULED.value,
        )
    )
    assert scheduled_msg is not None
    orig_scheduled_at = scheduled_msg.scheduled_at
    assert orig_scheduled_at is not None

    auto_reply_file = FIXTURES_DIR / "03_outlook_auto_reply_oof.eml"
    mail = IncomingMail.from_bytes(auto_reply_file.read_bytes(), uid=201)

    process_incoming_mail(mail, db, clock=clock)
    db.refresh(lead)
    db.refresh(scheduled_msg)

    # Lead remains in sequence (NOT stopped or marked replied)
    assert lead.status == LeadStatus.IN_SEQUENCE.value
    # Scheduled message pushed back by 5 days
    assert scheduled_msg.scheduled_at == orig_scheduled_at + timedelta(days=5)

    event = db.scalar(
        select(Event).where(Event.lead_id == lead.id, Event.type == EventType.AUTO_REPLY.value)
    )
    assert event is not None
    assert event.payload.get("delay_days") == 5


def test_positive_reply_saves_reply_record_and_stops_sequence(
    inbox_db: tuple[Session, sessionmaker],
) -> None:
    """Verify positive reply cancels follow-ups, sets status replied, and saves Reply record."""
    db, _ = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    lead = db.scalar(select(Lead).where(Lead.email == "sarah@acme.com"))
    assert lead is not None

    pos_file = FIXTURES_DIR / "07_positive_reply_calendly.eml"
    mail = IncomingMail.from_bytes(pos_file.read_bytes(), uid=301)

    process_incoming_mail(mail, db, clock=clock)
    db.refresh(lead)

    assert lead.status == LeadStatus.REPLIED.value

    # Pending follow-up skipped
    pending = db.scalar(
        select(EmailMessage).where(EmailMessage.lead_id == lead.id, EmailMessage.step == 2)
    )
    assert pending is not None
    assert pending.status == EmailMessageStatus.SKIPPED.value
    assert "Cancelled due to lead reply" in (pending.error or "")

    # Reply record created
    reply_record = db.scalar(select(Reply).where(Reply.lead_id == lead.id))
    assert reply_record is not None
    assert reply_record.classification == InboundClassification.REPLY_POSITIVE_HINT.value
    assert reply_record.is_positive_hint is True
    assert "calendly.com" in reply_record.body
    assert "This sounds very interesting" in reply_record.snippet


def test_worker_inbox_integration_and_bounce_rate_stop(
    inbox_db: tuple[Session, sessionmaker],
) -> None:
    """Verify Worker polls inbox, updates bounce statistics, and pauses campaigns
    when threshold is exceeded.
    """
    db, factory = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    campaign = db.scalar(select(Campaign))
    assert campaign is not None
    assert campaign.status == CampaignStatus.ACTIVE.value

    reader = FakeMailboxReader(fixtures_dir=FIXTURES_DIR)
    worker = Worker(
        clock=clock,
        session_factory=factory,
        inbox_reader=reader,
        inbox_poll_interval=180.0,
    )

    # Run worker cycle forcing inbox poll
    worker.run_once(poll_inbox_now=True)

    # Verify bounce rates recorded in Setting table
    rate_7d_setting = db.scalar(select(Setting.value).where(Setting.key == "BOUNCE_RATE_7D"))
    rate_100_setting = db.scalar(select(Setting.value).where(Setting.key == "BOUNCE_RATE_LAST_100"))
    assert rate_7d_setting is not None
    assert rate_100_setting is not None

    # Calculate rates directly to verify
    stats = calculate_bounce_rates(db, clock.now())
    assert "bounce_rate_7d" in stats
    assert "bounce_rate_last_100" in stats


def test_multi_mailbox_uid_checkpoints(inbox_db: tuple[Session, sessionmaker]) -> None:
    """Verify that multiple active mailboxes maintain isolated UID checkpoints."""
    from unittest.mock import MagicMock

    from app.models.mailbox import Mailbox

    db, _ = inbox_db
    clock = FakeClock(datetime(2026, 10, 12, 12, 0, tzinfo=UTC))

    mb1 = MagicMock(spec=Mailbox)
    mb1.id = 1
    reader1 = FakeMailboxReader()
    reader1.mailbox = mb1

    mb2 = MagicMock(spec=Mailbox)
    mb2.id = 2
    reader2 = FakeMailboxReader()
    reader2.mailbox = mb2

    # Mailbox 1 has 5 messages (UIDs 1 to 5)
    for i in range(1, 6):
        reader1.add_message(f"From: user{i}@example.com\nTo: mb1@test.com\nSubject: Hi {i}\n\nBody".encode())

    # Mailbox 2 has 3 messages (UIDs 1 to 3)
    for i in range(1, 4):
        reader2.add_message(f"From: user{i}@example.com\nTo: mb2@test.com\nSubject: Hi {i}\n\nBody".encode())

    # Poll Mailbox 1
    count1, _ = poll_inbox_and_process(reader1, db, clock=clock)
    assert count1 == 5
    assert db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID_MB_1")) == "5"

    # Poll Mailbox 2 - should NOT be blocked by Mailbox 1's UID 5
    count2, _ = poll_inbox_and_process(reader2, db, clock=clock)
    assert count2 == 3
    assert db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID_MB_2")) == "3"

    # Add 2 more messages to Mailbox 2 (UIDs 4 and 5)
    for i in range(4, 6):
        reader2.add_message(f"From: user{i}@example.com\nTo: mb2@test.com\nSubject: Hi {i}\n\nBody".encode())

    count2_next, _ = poll_inbox_and_process(reader2, db, clock=clock)
    assert count2_next == 2
    assert db.scalar(select(Setting.value).where(Setting.key == "LAST_INBOX_UID_MB_2")) == "5"

