"""Unit and integration tests for email sender implementations.

Covers:
- DryRunSender: zero socket connections, full outbox logging and file persistence.
- SmtpSender: local aiosmtpd server test (headers, threading, plain text, Message-ID).
- Message building formatting and headers.
"""

import socket
from email import message_from_bytes
from email.message import EmailMessage as PyEmailMessage
from email.policy import default
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiosmtpd.controller import Controller
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models.outbox import OutboxEmail
from app.services.sender import (
    DryRunSender,
    OutgoingMail,
    SmtpSender,
    build_email_message,
)


class RecordingHandler:
    """In-memory SMTP server handler capturing raw messages."""

    def __init__(self) -> None:
        self.envelopes: list[Any] = []
        self.parsed_messages: list[PyEmailMessage] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802
        self.envelopes.append(envelope)
        parsed = message_from_bytes(envelope.content, policy=default)
        self.parsed_messages.append(parsed)
        return "250 Message accepted for delivery"


@pytest.fixture
def test_db() -> Session:
    """Create in-memory SQLite database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_build_email_message_formatting() -> None:
    """Verify plain-text, UTF-8 email formatting, headers, and absence of HTML/tracking."""
    mail = OutgoingMail(
        to_email="lead@company.com",
        to_name="Jane Doe",
        from_email="alex@agenticsoftware.com",
        from_name="Alex Mercer",
        subject="Streamlining operations at Acme",
        body="Hi Jane,\n\nThought this automation idea might help.\n\nBest,\nAlex",
        in_reply_to="<initial-step1@agenticsoftware.com>",
        references="<initial-step1@agenticsoftware.com>",
        step=2,
    )

    msg, msg_id = build_email_message(mail)

    assert msg.get_content_type() == "text/plain"
    assert msg.get_param("charset") == "utf-8"
    assert "Alex Mercer <alex@agenticsoftware.com>" in msg["From"]
    assert "Jane Doe <lead@company.com>" in msg["To"]
    assert msg["Subject"] == "Streamlining operations at Acme"
    assert msg["Reply-To"] == "alex@agenticsoftware.com"
    assert msg["Message-ID"] == msg_id
    assert "@agenticsoftware.com" in msg_id
    assert msg["In-Reply-To"] == "<initial-step1@agenticsoftware.com>"
    assert msg["References"] == "<initial-step1@agenticsoftware.com>"
    assert "<mailto:alex@agenticsoftware.com?subject=unsubscribe>" in msg["List-Unsubscribe"]

    # Verify plain text body
    content = msg.get_content()
    assert "Thought this automation idea might help" in content
    assert "<html>" not in content.lower()
    assert "<img" not in content.lower()


def test_dry_run_sender_never_opens_socket(
    test_db: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Verify DryRunSender writes outbox records and files without opening any network sockets."""
    socket_mock = MagicMock()
    monkeypatch.setattr(socket, "socket", socket_mock)

    sender = DryRunSender(db=test_db, outbox_dir=tmp_path / "outbox")

    mail = OutgoingMail(
        to_email="dryrun@target.com",
        to_name="Dry Run Target",
        from_email="operator@test.com",
        from_name="Operator",
        subject="Dry-run outreach verification",
        body="This email must never touch a socket.",
        email_message_id=42,
    )

    result = sender.send(mail)

    assert result.success is True
    assert result.dry_run is True
    assert result.message_id is not None

    # Assert NO socket was ever constructed or opened
    assert not socket_mock.called

    # Assert record in outbox table
    record = test_db.scalar(select(OutboxEmail).where(OutboxEmail.to_email == "dryrun@target.com"))
    assert record is not None
    assert record.email_message_id == 42
    assert record.status == "sent (dry-run)"
    assert record.subject == "Dry-run outreach verification"
    assert "This email must never touch a socket" in record.body
    assert record.headers is not None
    assert record.raw_eml is not None

    # Assert file in filesystem outbox directory
    outbox_files = list((tmp_path / "outbox").glob("*.eml"))
    assert len(outbox_files) == 1
    file_content = outbox_files[0].read_text(encoding="utf-8")
    assert "Dry-run outreach verification" in file_content


def test_smtp_sender_integration_with_local_aiosmtpd() -> None:
    """Integration test SmtpSender against a local aiosmtpd server on 127.0.0.1 without SSL."""
    # Find free port for Windows reliability
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]

    handler = RecordingHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=free_port)
    controller.start()

    try:
        sender = SmtpSender(
            host="127.0.0.1",
            port=free_port,
            use_ssl=False,
            timeout=5.0,
        )

        # 1. Dispatch Step 1 Email
        mail_step1 = OutgoingMail(
            to_email="client@localcorp.test",
            to_name="Bob Client",
            from_email="founder@devstudio.test",
            from_name="Founder",
            subject="Automation ideas for LocalCorp",
            body="Hello Bob,\n\nWanted to share an operational idea.\n\nBest,\nFounder",
            step=1,
        )

        res1 = sender.send(mail_step1)
        assert res1.success is True
        assert res1.message_id is not None
        step1_id = res1.message_id

        # 2. Dispatch Step 2 Follow-Up Email referencing Step 1
        mail_step2 = OutgoingMail(
            to_email="client@localcorp.test",
            to_name="Bob Client",
            from_email="founder@devstudio.test",
            from_name="Founder",
            subject="Re: Automation ideas for LocalCorp",
            body="Hi Bob,\n\nFollowing up on my previous note.\n\nBest,\nFounder",
            in_reply_to=step1_id,
            references=step1_id,
            step=2,
        )

        res2 = sender.send(mail_step2)
        assert res2.success is True
        assert res2.message_id is not None
        step2_id = res2.message_id

        # Verify uniqueness of Message-IDs
        assert step1_id != step2_id

        # Verify messages captured by local aiosmtpd server
        assert len(handler.parsed_messages) == 2

        msg1 = handler.parsed_messages[0]
        assert "Founder <founder@devstudio.test>" in msg1["From"]
        assert "Bob Client <client@localcorp.test>" in msg1["To"]
        assert msg1["Subject"] == "Automation ideas for LocalCorp"
        assert msg1.get_content_type() == "text/plain"
        assert "Wanted to share an operational idea" in msg1.get_content()

        msg2 = handler.parsed_messages[1]
        assert msg2["Subject"] == "Re: Automation ideas for LocalCorp"
        assert msg2["In-Reply-To"] == step1_id
        assert msg2["References"] == step1_id
        assert msg2["Message-ID"] == step2_id
        assert "Following up on my previous note" in msg2.get_content()

    finally:
        controller.stop()
