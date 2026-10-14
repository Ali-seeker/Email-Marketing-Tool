"""System settings inspection and update API endpoints."""

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import SettingsUpdateRequest, TestEmailRequest
from app.core.config import BASE_DIR, get_settings
from app.core.logging import get_logger
from app.models.setting import Setting
from app.services.sender import OutgoingMail, SmtpSender

logger = get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["Settings"])


def is_live_mode_active(db: Session | None = None) -> bool:
    """Return whether LIVE_MODE is currently enabled via DB override or .env."""
    if db is not None:
        val = db.scalar(select(Setting.value).where(Setting.key == "LIVE_MODE"))
        if val is not None:
            return val.strip().lower() in ("true", "1", "yes")
    return bool(get_settings().LIVE_MODE)


# Strictly whitelisted non-sensitive settings allowed to be inspected and modified
WHITELISTED_SETTINGS = {
    "OUTREACH_START_DATE": str,
    "HARD_MAX_PER_DAY": int,
    "MAX_EMAILS_PER_DAY_PER_MAILBOX": int,
    "SEND_DAYS": list,
    "SEND_WINDOW_START": str,
    "SEND_WINDOW_END": str,
    "MIN_GAP_SECONDS": int,
    "MAX_JITTER_SECONDS": int,
    "MAILBOX_TZ": str,
    "ALLOW_RISKY": bool,
    "REQUIRE_PERSONAL_NOTE": bool,
    "DEFAULT_SERVICE": str,
    "DEFAULT_EXAMPLE_RESULT": str,
    "BOUNCE_RATE_STOP_THRESHOLD_PERCENT": float,
    "AUTO_APPROVE": bool,
    "LLM_PROVIDER": str,
    "LLM_DAILY_CALL_CAP": int,
}

HARD_MAX_LIMIT = 30


@router.get("")
def get_whitelisted_settings(
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve current values for all whitelisted non-secret system settings."""
    app_settings = get_settings()

    # Load any database overrides
    db_settings = db.scalars(select(Setting)).all()
    overrides = {s.key: s.value for s in db_settings}

    result: dict[str, Any] = {}
    for key, expected_type in WHITELISTED_SETTINGS.items():
        if key in overrides:
            raw_val = overrides[key]
            # Deserialize JSON / typed values
            try:
                if expected_type in (list, dict, bool):
                    val = json.loads(raw_val)
                elif expected_type is int:
                    val = int(raw_val)
                elif expected_type is float:
                    val = float(raw_val)
                else:
                    val = raw_val
            except Exception:
                val = raw_val
            result[key] = val
        else:
            # Fall back to config object attribute
            result[key] = getattr(app_settings, key, None)

    result["LIVE_MODE"] = is_live_mode_active(db)
    return result


@router.put("")
def update_whitelisted_settings(
    payload: SettingsUpdateRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Update whitelisted settings with strict bounds and safety validation.

    Rules:
    - No daily send cap may exceed HARD_MAX_PER_DAY (30).
    - Lowering safety throttles below defaults (e.g. MIN_GAP_SECONDS < 120 or
      BOUNCE_RATE_STOP_THRESHOLD_PERCENT > 2.0) requires explicit `confirm=True`.
    """
    for key, val in payload.settings.items():
        if key not in WHITELISTED_SETTINGS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Setting '{key}' is not editable or not whitelisted.",
            )

        # 1. Cap validation: cannot exceed HARD_MAX_LIMIT (30)
        if key in ("HARD_MAX_PER_DAY", "MAX_EMAILS_PER_DAY_PER_MAILBOX"):
            try:
                num_val = int(val)
                if num_val > HARD_MAX_LIMIT:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Setting '{key}' value ({num_val}) exceeds "
                            f"HARD_MAX_PER_DAY limit of {HARD_MAX_LIMIT}."
                        ),
                    )
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid integer value for {key}: {exc}",
                ) from exc

        # 2. Safety limits relaxation check
        if key == "MIN_GAP_SECONDS":
            try:
                gap_val = int(val)
                if gap_val < 120 and not payload.confirm:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Lowering {key} below 120s ({gap_val}s) reduces spam safety. "
                            "Requires confirm=true."
                        ),
                    )
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

        if key == "BOUNCE_RATE_STOP_THRESHOLD_PERCENT":
            try:
                threshold_val = float(val)
                if threshold_val > 2.0 and not payload.confirm:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=(
                            f"Raising {key} above 2.0% ({threshold_val}%) reduces "
                            "domain protection. Requires confirm=true."
                        ),
                    )
            except (ValueError, TypeError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc

    # Persist validated settings into Setting table
    for key, val in payload.settings.items():
        if isinstance(val, (dict, list, bool)):
            str_val = json.dumps(val)
        else:
            str_val = str(val)

        db.merge(Setting(key=key, value=str_val))

    db.commit()

    return {
        "status": "updated",
        "updated_keys": list(payload.settings.keys()),
        "settings": get_whitelisted_settings(db=db),
    }


@router.post("/test-email")
def send_test_email(
    payload: TestEmailRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Send an immediate real test email through SMTP to verify credentials and inbox delivery."""
    get_settings.cache_clear()
    cfg = get_settings()

    host = payload.smtp_host or cfg.SMTP_HOST
    port = payload.smtp_port or cfg.SMTP_PORT
    username = payload.smtp_username or cfg.SMTP_USERNAME
    pwd = payload.smtp_password or (
        cfg.SMTP_PASSWORD.get_secret_value() if cfg.SMTP_PASSWORD else None
    )
    from_email = (
        payload.from_email
        or cfg.SMTP_FROM_EMAIL
        or username
        or "coldreach@localhost"
    )
    from_name = cfg.SMTP_FROM_NAME or "ColdReach Tester"

    if not host or host == "smtp.example.com":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="SMTP host is not configured yet. Please configure SMTP_HOST, SMTP_USERNAME, and SMTP_PASSWORD in your .env file.",
        )

    sender = SmtpSender(
        host=host,
        port=port,
        username=username,
        password=pwd,
        use_ssl=(port == 465),
    )

    outgoing = OutgoingMail(
        to_email=payload.to_email,
        from_email=from_email,
        from_name=from_name,
        subject="[ColdReach] Live SMTP Test - Delivery Successful!",
        body=(
            f"Hello!\n\n"
            f"Congratulations! This test email was successfully dispatched from ColdReach directly via your SMTP server.\n\n"
            f"• SMTP Server: {host}:{port}\n"
            f"• Sender Address: {from_email}\n"
            f"• Recipient: {payload.to_email}\n\n"
            f"Your personal mail delivery is fully operational and ready for campaign outreach!"
        ),
    )

    result = sender.send(outgoing)
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"SMTP delivery failed: {result.error} (type: {result.error_type})",
        )

    return {
        "success": True,
        "message_id": result.message_id,
        "recipient": payload.to_email,
        "detail": f"Test email sent successfully to {payload.to_email} via {host}:{port}",
    }


@router.post("/toggle-live-mode")
def toggle_live_mode(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Toggle LIVE_MODE vs DRY-RUN safely from the operator desk UI.

    Updates the database Setting override, persists to the .env file,
    and updates the in-memory runtime configuration.
    """
    new_state = bool(payload.get("live_mode", False))

    # 1. Update Database override
    db.merge(Setting(key="LIVE_MODE", value="true" if new_state else "false"))
    db.commit()

    # 2. Update .env file on disk if present
    env_path = BASE_DIR / ".env"
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8")
            if re.search(r"^LIVE_MODE=.*$", content, flags=re.MULTILINE):
                new_content = re.sub(
                    r"^LIVE_MODE=.*$",
                    f"LIVE_MODE={'true' if new_state else 'false'}",
                    content,
                    flags=re.MULTILINE,
                )
            else:
                new_content = content.rstrip() + f"\nLIVE_MODE={'true' if new_state else 'false'}\n"
            env_path.write_text(new_content, encoding="utf-8")
        except Exception as e:
            logger.warning("Could not persist LIVE_MODE to .env file: %s", e)

    # 3. Clear settings cache and update memory
    get_settings.cache_clear()
    cfg = get_settings()
    cfg.LIVE_MODE = new_state

    return {
        "live_mode": new_state,
        "message": (
            "Activated LIVE mode. Real emails will leave this machine via SMTP."
            if new_state
            else "Activated DRY-RUN mode. Outbound emails will remain safely local."
        ),
    }
