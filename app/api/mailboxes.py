"""Mailbox management API endpoints for multi-account sending and rotation."""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import (
    MailboxCreateRequest,
    MailboxResponse,
    MailboxUpdateRequest,
)
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.mailbox import Mailbox
from app.services.planner import get_mailbox_tz
from app.services.sender import OutgoingMail, SmtpSender

router = APIRouter(prefix="/mailboxes", tags=["Mailboxes"])


def _calculate_mailbox_stats(m: Mailbox, db: Session) -> tuple[int, int]:
    """Calculate sent_today and remaining_today for a mailbox."""
    mailbox_tz = get_mailbox_tz(db)
    now_local = datetime.now(UTC).astimezone(mailbox_tz).date()

    stmt = select(func.count(EmailMessage.id)).where(
        EmailMessage.mailbox_id == m.id,
        EmailMessage.status.in_(
            (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
        ),
    )
    sent_msgs = db.scalars(
        select(EmailMessage).where(
            EmailMessage.mailbox_id == m.id,
            EmailMessage.status.in_(
                (EmailMessageStatus.SENT.value, EmailMessageStatus.SENT_DRY_RUN.value)
            ),
        )
    ).all()

    sent_today = 0
    for em in sent_msgs:
        ref_dt = em.sent_at or em.scheduled_at
        if ref_dt:
            if ref_dt.tzinfo is None:
                ref_dt = ref_dt.replace(tzinfo=UTC)
            if ref_dt.astimezone(mailbox_tz).date() == now_local:
                sent_today += 1

    remaining = max(0, m.daily_send_cap - sent_today)
    return sent_today, remaining


@router.get("", response_model=list[MailboxResponse])
def list_mailboxes(db: Session = Depends(get_db)) -> list[MailboxResponse]:
    """List all registered sending mailboxes with their live dispatch capacity."""
    mailboxes = db.scalars(select(Mailbox).order_by(Mailbox.id.asc())).all()
    results: list[MailboxResponse] = []

    for m in mailboxes:
        sent_today, remaining = _calculate_mailbox_stats(m, db)
        resp = MailboxResponse.model_validate(m)
        resp.sent_today = sent_today
        resp.remaining_today = remaining
        results.append(resp)

    return results


@router.post("", response_model=MailboxResponse, status_code=status.HTTP_201_CREATED)
def create_mailbox(
    payload: MailboxCreateRequest,
    db: Session = Depends(get_db),
) -> MailboxResponse:
    """Register a new sending mailbox for multi-account pool rotation."""
    existing = db.scalar(select(Mailbox).where(Mailbox.email == payload.email.strip().lower()))
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Mailbox with email '{payload.email}' already exists.",
        )

    # Clean username and password (strip spaces from Google App Passwords)
    clean_pwd = payload.smtp_password.replace(" ", "").strip()
    clean_user = (payload.smtp_username or payload.email).strip()
    clean_imap_user = (payload.imap_username or clean_user).strip()
    clean_imap_pwd = (payload.imap_password or clean_pwd).replace(" ", "").strip()

    mailbox = Mailbox(
        email=payload.email.strip().lower(),
        display_name=payload.display_name.strip() or "ColdReach Operator",
        smtp_host=payload.smtp_host.strip(),
        smtp_port=payload.smtp_port,
        smtp_username=clean_user,
        smtp_password=clean_pwd,
        smtp_use_ssl=(payload.smtp_port == 465),
        imap_host=payload.imap_host.strip(),
        imap_port=payload.imap_port,
        imap_username=clean_imap_user,
        imap_password=clean_imap_pwd,
        imap_use_ssl=(payload.imap_port == 993),
        daily_send_cap=payload.daily_send_cap,
        is_active=payload.is_active,
    )
    db.add(mailbox)
    db.commit()
    db.refresh(mailbox)

    resp = MailboxResponse.model_validate(mailbox)
    resp.sent_today = 0
    resp.remaining_today = mailbox.daily_send_cap
    return resp


@router.put("/{mailbox_id}", response_model=MailboxResponse)
def update_mailbox(
    mailbox_id: int,
    payload: MailboxUpdateRequest,
    db: Session = Depends(get_db),
) -> MailboxResponse:
    """Update settings, credentials, or active status of a mailbox."""
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mailbox {mailbox_id} not found.",
        )

    if payload.display_name is not None:
        mailbox.display_name = payload.display_name.strip()
    if payload.smtp_host is not None:
        mailbox.smtp_host = payload.smtp_host.strip()
    if payload.smtp_port is not None:
        mailbox.smtp_port = payload.smtp_port
        mailbox.smtp_use_ssl = payload.smtp_port == 465
    if payload.smtp_username is not None:
        mailbox.smtp_username = payload.smtp_username.strip()
    if payload.smtp_password is not None and payload.smtp_password.strip():
        mailbox.smtp_password = payload.smtp_password.replace(" ", "").strip()
    if payload.imap_host is not None:
        mailbox.imap_host = payload.imap_host.strip()
    if payload.imap_port is not None:
        mailbox.imap_port = payload.imap_port
        mailbox.imap_use_ssl = payload.imap_port == 993
    if payload.imap_username is not None:
        mailbox.imap_username = payload.imap_username.strip()
    if payload.imap_password is not None and payload.imap_password.strip():
        mailbox.imap_password = payload.imap_password.replace(" ", "").strip()
    if payload.daily_send_cap is not None:
        mailbox.daily_send_cap = payload.daily_send_cap
    if payload.is_active is not None:
        mailbox.is_active = payload.is_active

    mailbox.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(mailbox)

    sent_today, remaining = _calculate_mailbox_stats(mailbox, db)
    resp = MailboxResponse.model_validate(mailbox)
    resp.sent_today = sent_today
    resp.remaining_today = remaining
    return resp


@router.delete("/{mailbox_id}")
def delete_mailbox(mailbox_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Delete a mailbox from the sending pool."""
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mailbox {mailbox_id} not found.",
        )

    db.delete(mailbox)
    db.commit()
    return {"success": True, "deleted_id": mailbox_id}


@router.post("/{mailbox_id}/test")
def test_mailbox_connection(
    mailbox_id: int,
    to_email: Annotated[str | None, Query(description="Destination test email")] = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Send an immediate test verification email using this specific mailbox."""
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Mailbox {mailbox_id} not found.",
        )

    dest_email = (to_email or mailbox.email).strip()
    sender = SmtpSender(
        host=mailbox.smtp_host,
        port=mailbox.smtp_port,
        username=mailbox.smtp_username,
        password=mailbox.smtp_password,
        use_ssl=mailbox.smtp_use_ssl,
    )

    outgoing = OutgoingMail(
        to_email=dest_email,
        from_email=mailbox.email,
        from_name=mailbox.display_name,
        subject=f"[ColdReach] Mailbox Verification - {mailbox.email}",
        body=(
            f"Hello!\n\n"
            f"This is a test verification email from ColdReach.\n\n"
            f"Mailbox: {mailbox.display_name} <{mailbox.email}>\n"
            f"SMTP Server: {mailbox.smtp_host}:{mailbox.smtp_port}\n"
            f"Target: {dest_email}\n\n"
            f"Your mailbox connection is active and sending successfully!"
        ),
    )

    result = sender.send(outgoing)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SMTP test failed for {mailbox.email}: {result.error} (type: {result.error_type})",
        )

    return {
        "success": True,
        "mailbox_id": mailbox.id,
        "mailbox_email": mailbox.email,
        "recipient": dest_email,
        "message_id": result.message_id,
        "detail": f"Test email successfully dispatched to {dest_email} via {mailbox.email}!",
    }
