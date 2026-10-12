"""Server-Sent Events (SSE) live updates endpoint."""

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.event import Event, EventType
from app.models.reply import Reply
from app.services.safety import is_killswitch_active

router = APIRouter(prefix="/events", tags=["Live Updates"])

# In-memory subscriber queues for instant pub/sub notifications
_event_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
_recent_events: list[dict[str, Any]] = []


def broadcast_live_event(event_type: str, data: dict[str, Any]) -> None:
    """Broadcast an event to all active SSE listener streams."""
    message = {"event": event_type, "data": data}
    _recent_events.append(message)
    if len(_recent_events) > 50:
        _recent_events.pop(0)
    for q in list(_event_subscribers):
        try:
            q.put_nowait(message)
        except Exception:
            pass


async def sse_event_stream(
    request: Request,
    db: Session,
    last_event_id: int = 0,
    max_events: int | None = None,
) -> AsyncGenerator[str, None]:
    """Asynchronous generator streaming live events in SSE format."""
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for item in _recent_events:
        queue.put_nowait(item)
    _event_subscribers.append(queue)

    events_sent = 0
    initial_ks = is_killswitch_active(db)

    # Initial connection handshake
    initial_payload = {
        "event": "connected",
        "data": {"status": "connected", "killswitch_active": initial_ks},
    }
    yield f"event: {initial_payload['event']}\ndata: {json.dumps(initial_payload['data'])}\n\n"
    events_sent += 1

    last_seen_event_id = last_event_id
    last_seen_reply_id = 0

    max_e = db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1))
    if max_e and last_seen_event_id == 0:
        last_seen_event_id = max_e
    max_r = db.scalar(select(Reply.id).order_by(Reply.id.desc()).limit(1))
    if max_r:
        last_seen_reply_id = max_r

    try:
        loops = 0
        while True:
            loops += 1
            if await request.is_disconnected():
                break

            if max_events is not None and events_sent >= max_events:
                break
            if max_events is not None and loops > 15:
                break

            # 1. Check in-memory broadcast queue first
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.1)
                yield f"event: {msg['event']}\ndata: {json.dumps(msg['data'])}\n\n"
                events_sent += 1
                if max_events is not None and events_sent >= max_events:
                    break
                continue
            except TimeoutError:
                pass

            # 2. Poll DB for new events and replies
            new_events = db.scalars(
                select(Event)
                .where(Event.id > last_seen_event_id)
                .order_by(Event.id.asc())
                .limit(10)
            ).all()

            for ev in new_events:
                last_seen_event_id = ev.id
                ev_type = "killswitch" if ev.type == EventType.KILLSWITCH.value else "event"
                payload = {
                    "id": ev.id,
                    "type": ev.type,
                    "payload": ev.payload,
                    "created_at": ev.created_at.isoformat() if ev.created_at else None,
                }
                yield f"event: {ev_type}\ndata: {json.dumps(payload)}\n\n"
                events_sent += 1

            new_replies = db.scalars(
                select(Reply)
                .where(Reply.id > last_seen_reply_id)
                .order_by(Reply.id.asc())
                .limit(10)
            ).all()

            for rep in new_replies:
                last_seen_reply_id = rep.id
                rep_payload = {
                    "id": rep.id,
                    "lead_id": rep.lead_id,
                    "from_email": rep.from_email,
                    "subject": rep.subject,
                    "classification": rep.classification,
                    "is_positive_hint": rep.is_positive_hint,
                }
                yield f"event: reply\ndata: {json.dumps(rep_payload)}\n\n"
                events_sent += 1

            # Keep-alive heartbeat ping
            yield ": ping\n\n"
            await asyncio.sleep(0.5)

    finally:
        if queue in _event_subscribers:
            _event_subscribers.remove(queue)


@router.get("")
async def stream_live_events(
    request: Request,
    last_event_id: int = Query(default=0, description="Resume stream from event ID"),
    max_events: int | None = Query(
        default=None, description="Max events before closing (for tests)"
    ),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Stream live events (new replies, killswitch status, alerts) via Server-Sent Events."""
    return StreamingResponse(
        sse_event_stream(request, db=db, last_event_id=last_event_id, max_events=max_events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
