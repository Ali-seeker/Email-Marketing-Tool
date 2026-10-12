"""System settings inspection and update API endpoints."""

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.schemas import SettingsUpdateRequest
from app.core.config import get_settings
from app.models.setting import Setting

router = APIRouter(prefix="/settings", tags=["Settings"])

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
