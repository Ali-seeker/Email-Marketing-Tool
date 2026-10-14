"""Health, safety monitor, and emergency kill-switch API endpoints."""

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_clock, get_db
from app.api.schemas import SafetyAlertItem, SafetyResumeRequest, SafetyStatusResponse
from app.core.clock import Clock
from app.models.event import Event, EventType
from app.services.safety import (
    calculate_health_metrics,
    evaluate_safety_rules,
    is_killswitch_active,
    trigger_killswitch,
)
from app.services.safety import (
    resume_killswitch as service_resume_killswitch,
)

router = APIRouter(prefix="/safety", tags=["Health & Safety"])


@router.get("/status", response_model=SafetyStatusResponse)
def get_safety_status(
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> SafetyStatusResponse:
    """Retrieve current kill-switch state, deliverability health metrics, and safety alerts."""
    clk_now = clock.now()
    if clk_now.tzinfo is None:
        clk_now = clk_now.replace(tzinfo=UTC)

    metrics = calculate_health_metrics(db, clk_now)
    level, reasons = evaluate_safety_rules(metrics)
    killswitch = is_killswitch_active(db)

    # Calculate overall health score (0-100)
    score = 100
    if metrics.bounce_rate_100 > 1.0:
        score -= int(metrics.bounce_rate_100 * 15)
    if metrics.failure_rate > 2.0:
        score -= int(metrics.failure_rate * 5)
    if metrics.complaint_signals_7d > 0:
        score -= metrics.complaint_signals_7d * 20
    if metrics.auth_or_block_errors > 0:
        score -= 40
    if killswitch:
        score = min(score, 20)
    score = max(0, min(100, score))

    # Fetch recent safety events / alerts
    alert_events = db.scalars(
        select(Event)
        .where(Event.type.in_([EventType.KILLSWITCH.value, EventType.ERROR.value]))
        .order_by(Event.created_at.desc())
        .limit(10)
    ).all()

    alert_items = [
        SafetyAlertItem(
            id=e.id,
            type=e.type,
            message=(e.payload or {}).get("reason")
            or (e.payload or {}).get("message")
            or str(e.payload),
            created_at=e.created_at,
        )
        for e in alert_events
    ]

    from app.models.setting import Setting
    from app.services.email_verifier import HunterVerifier

    hunter_exceeded_setting = db.scalar(
        select(Setting.value).where(Setting.key == "HUNTER_QUOTA_EXCEEDED")
    )
    hunter_quota_exceeded = bool(
        hunter_exceeded_setting
        and hunter_exceeded_setting.strip().lower() in ("true", "1", "yes")
    )

    hunter_dismissed_setting = db.scalar(
        select(Setting.value).where(Setting.key == "HUNTER_QUOTA_ALERT_DISMISSED")
    )
    hunter_quota_dismissed = bool(
        hunter_dismissed_setting
        and hunter_dismissed_setting.strip().lower() in ("true", "1", "yes")
    )

    hv = HunterVerifier()
    hunter_used_credits = hv.get_used_credits(db, clock)
    hunter_monthly_quota = hv.monthly_quota

    return SafetyStatusResponse(
        killswitch_active=killswitch,
        health_score=score,
        severity=level,
        bounce_rate_100=metrics.bounce_rate_100,
        bounce_rate_7d=metrics.bounce_rate_7d,
        failure_rate=metrics.failure_rate,
        complaint_signals_7d=metrics.complaint_signals_7d,
        unsubscribe_rate_100=metrics.unsubscribe_rate_100,
        auth_or_block_errors=metrics.auth_or_block_errors,
        sample_size_ok=metrics.sample_size_ok,
        reasons=reasons,
        recent_alerts=alert_items,
        hunter_quota_exceeded=hunter_quota_exceeded,
        hunter_quota_dismissed=hunter_quota_dismissed,
        hunter_used_credits=hunter_used_credits,
        hunter_monthly_quota=hunter_monthly_quota,
    )


@router.post("/dismiss-hunter-alert")
def dismiss_hunter_alert(
    db: Session = Depends(get_db), clock: Clock = Depends(get_clock)
) -> dict[str, str]:
    """Dismiss persistent Hunter.io quota alert on operator request."""
    from app.models.setting import Setting

    now_dt = clock.now()
    dismissed = db.scalar(
        select(Setting).where(Setting.key == "HUNTER_QUOTA_ALERT_DISMISSED")
    )
    if not dismissed:
        db.add(Setting(key="HUNTER_QUOTA_ALERT_DISMISSED", value="true", updated_at=now_dt))
    else:
        dismissed.value = "true"
        dismissed.updated_at = now_dt
    db.commit()
    return {"status": "dismissed", "message": "Hunter quota alert dismissed by operator."}


@router.post("/resume")
def resume_killswitch(
    payload: SafetyResumeRequest,
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Resume outbound sending from emergency kill-switch state.

    Requires explicit user acknowledgement phrase ('I ACKNOWLEDGE',
    'I ACKNOWLEDGE AND RESUME', or 'I UNDERSTAND AND ACCEPT RISK') and
    a descriptive explanation note (>= 5 chars). Resumption halves dispatch
    capacity for the following 3 days.
    """
    try:
        service_resume_killswitch(
            acknowledgement=payload.acknowledgement,
            note=payload.note,
            db=db,
            clock=clock,
        )
        return {
            "status": "resumed",
            "message": "Kill-switch successfully disarmed. Capacity halved for 3 calendar days.",
            "note": payload.note.strip(),
        }
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@router.post("/killswitch")
def engage_killswitch(
    reason: str = "Manually triggered emergency killswitch by operator",
    db: Session = Depends(get_db),
    clock: Clock = Depends(get_clock),
) -> dict[str, object]:
    """Manually trip emergency kill-switch to halt all outbound sending."""
    trigger_killswitch(reason=reason, db=db, clock=clock)
    return {
        "status": "engaged",
        "message": "Emergency kill-switch active. All outbound worker sending halted immediately.",
        "reason": reason,
    }
