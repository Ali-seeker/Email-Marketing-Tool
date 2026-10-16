"""Comprehensive tests for FastAPI REST API endpoints."""

from collections.abc import Generator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_clock, get_db
from app.api.events import broadcast_live_event
from app.core.clock import FakeClock
from app.core.config import Settings, get_settings
from app.core.db import Base
from app.main import create_app
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus, RiskLevel
from app.models.outbox import OutboxEmail
from app.models.reply import Reply
from app.models.setting import Setting
from app.services.safety import trigger_killswitch

TEST_API_TOKEN = "test-super-secret-api-token-999"
TEST_SMTP_PASS = "smtp-secret-pass-xyz123"
TEST_GEMINI_KEY = "gemini-api-key-ai-xyz"


@pytest.fixture
def test_settings() -> Settings:
    """Fixture providing test configuration settings with known secrets."""
    settings = get_settings()
    settings.API_TOKEN = SecretStr(TEST_API_TOKEN)
    settings.SMTP_PASSWORD = SecretStr(TEST_SMTP_PASS)
    settings.GEMINI_API_KEY = SecretStr(TEST_GEMINI_KEY)
    settings.LIVE_MODE = False
    return settings


@pytest.fixture
def test_db_session() -> Generator[tuple[Session, sessionmaker], None, None]:
    """In-memory SQLite database session fixture."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with sm() as session:
        # Seed initial campaign and settings
        campaign = Campaign(
            name="Test Campaign", niche="AI SaaS", status=CampaignStatus.ACTIVE.value
        )
        session.add(campaign)
        session.add(Setting(key="OUTREACH_START_DATE", value="2026-10-01"))
        session.add(Setting(key="MAILBOX_TZ", value="UTC"))
        session.commit()
        yield session, sm


@pytest.fixture
def client(
    test_db_session: tuple[Session, sessionmaker],
    test_settings: Settings,
) -> Generator[TestClient, None, None]:
    """TestClient configured with in-memory DB and fake clock."""
    session, sm = test_db_session
    clock = FakeClock(datetime(2026, 10, 15, 10, 0, 0, tzinfo=UTC))

    app = create_app()

    def override_get_db() -> Generator[Session, None, None]:
        s = sm()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_clock] = lambda: clock

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers() -> dict[str, str]:
    """Standard authorized request headers."""
    return {"X-API-Token": TEST_API_TOKEN}


# =============================================================================
# 1. Security & Authentication Tests
# =============================================================================
def test_auth_required_on_all_v1_routes(client: TestClient) -> None:
    """All v1 endpoints must reject unauthenticated requests with HTTP 401."""
    protected_urls = [
        "/api/v1/campaigns",
        "/api/v1/leads",
        "/api/v1/approval/pending",
        "/api/v1/planning/preview",
        "/api/v1/planning/capacity",
        "/api/v1/emails",
        "/api/v1/emails/outbox",
        "/api/v1/replies",
        "/api/v1/templates/sequence?campaign_id=1",
        "/api/v1/suppression",
        "/api/v1/stats/funnel",
        "/api/v1/safety/status",
        "/api/v1/settings",
    ]

    for url in protected_urls:
        resp = client.get(url)
        assert resp.status_code == 401, (
            f"Expected 401 on unauthenticated GET {url}, got {resp.status_code}"
        )

    # Invalid token check
    resp_bad = client.get("/api/v1/campaigns", headers={"X-API-Token": "wrong-token"})
    assert resp_bad.status_code == 401

    # Public health check endpoint does not require token
    resp_health = client.get("/api/health")
    assert resp_health.status_code == 200


def test_secrets_never_leak_in_json_responses(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Ensure no endpoint leaks sensitive secrets, tokens, or passwords."""
    endpoints = [
        "/api/health",
        "/api/v1/campaigns",
        "/api/v1/leads",
        "/api/v1/approval/pending",
        "/api/v1/planning/capacity",
        "/api/v1/emails",
        "/api/v1/replies",
        "/api/v1/suppression",
        "/api/v1/stats/funnel",
        "/api/v1/stats/health",
        "/api/v1/safety/status",
        "/api/v1/settings",
    ]

    sensitive_tokens = [
        TEST_API_TOKEN,
        TEST_SMTP_PASS,
        TEST_GEMINI_KEY,
    ]

    for ep in endpoints:
        resp = client.get(ep, headers=auth_headers)
        assert resp.status_code == 200, f"Failed GET {ep}"
        body_text = resp.text
        for secret in sensitive_tokens:
            assert secret not in body_text, f"Secret leaked in endpoint {ep} response!"


# =============================================================================
# 2. Campaigns API Tests
# =============================================================================
def test_campaigns_crud_and_status_flow(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Test campaign creation, listing, duplicate rejection, and state transitions."""
    # 1. Create campaign
    create_resp = client.post(
        "/api/v1/campaigns",
        json={"name": "Outreach Alpha", "niche": "Fintech"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    camp_data = create_resp.json()
    camp_id = camp_data["id"]
    assert camp_data["name"] == "Outreach Alpha"

    # 2. Duplicate campaign rejected (400)
    dup_resp = client.post(
        "/api/v1/campaigns",
        json={"name": "Outreach Alpha", "niche": "Fintech"},
        headers=auth_headers,
    )
    assert dup_resp.status_code == 400

    # 3. Get campaign detail
    get_resp = client.get(f"/api/v1/campaigns/{camp_id}", headers=auth_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["name"] == "Outreach Alpha"

    # 4. Get non-existent campaign (404)
    missing_resp = client.get("/api/v1/campaigns/9999", headers=auth_headers)
    assert missing_resp.status_code == 404

    # 5. Update campaign
    patch_resp = client.patch(
        f"/api/v1/campaigns/{camp_id}",
        json={"niche": "Wealthtech"},
        headers=auth_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["niche"] == "Wealthtech"

    # 6. Status transitions: pause -> activate
    pause_resp = client.post(f"/api/v1/campaigns/{camp_id}/pause", headers=auth_headers)
    assert pause_resp.status_code == 200
    assert pause_resp.json()["status"] == CampaignStatus.PAUSED.value

    act_resp = client.post(f"/api/v1/campaigns/{camp_id}/activate", headers=auth_headers)
    assert act_resp.status_code == 200
    assert act_resp.json()["status"] == CampaignStatus.ACTIVE.value


# =============================================================================
# 3. Leads API & CSV Upload Tests
# =============================================================================
def test_leads_csv_upload_messy_fixture(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Upload messy CSV fixture and verify import report."""
    messy_csv = (
        "Email Address , First Name , Last Name , Company Name , URL , Location , Personal Note\n"
        "alice@acme.com, Alice , Smith , Acme Corp , https://acme.com , US , Loved your blog\n"
        "bob@beta.io, Bob , Jones , Beta LLC , beta.io , United Kingdom , Great platform\n"
        "invalid-email-address, Bad , Lead , Broken , broken.org , Canada , none\n"
        "alice@acme.com, Duplicate , Alice , Acme Corp , https://acme.com , US , duplicate\n"
    )

    files = {"file": ("leads.csv", messy_csv.encode("utf-8"), "text/csv")}
    resp = client.post(
        "/api/v1/leads/upload",
        data={"campaign_name": "Test Campaign"},
        files=files,
        headers=auth_headers,
    )
    assert resp.status_code == 200
    report = resp.json()
    assert report["total_rows"] == 4
    assert report["imported"] == 2
    assert report["duplicates"] == 1
    assert report["rejected"] == 1


def test_leads_list_pagination_and_filters(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test leads listing with search text, status filter, and pagination."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))

    # Add sample leads
    lead1 = Lead(
        campaign_id=camp.id,
        email="developer@techco.com",
        first_name="David",
        last_name="Miller",
        company="TechCo Inc",
        country="US",
        status=LeadStatus.VALID.value,
        risk_level=RiskLevel.OK.value,
    )
    lead2 = Lead(
        campaign_id=camp.id,
        email="cto@startup.io",
        first_name="Claire",
        last_name="Underwood",
        company="Startup IO",
        country="UK",
        status=LeadStatus.PERSONALIZED.value,
        risk_level=RiskLevel.OK.value,
        personal_note="Saw your scalable cloud infra.",
        evidence_quote="scalable cloud infra",
    )
    session.add_all([lead1, lead2])
    session.commit()

    # 1. Search filter
    search_resp = client.get(
        "/api/v1/leads?search=David",
        headers=auth_headers,
    )
    assert search_resp.status_code == 200
    items = search_resp.json()["items"]
    assert len(items) == 1
    assert items[0]["email"] == "developer@techco.com"

    # 2. Status filter
    status_resp = client.get(
        f"/api/v1/leads?status={LeadStatus.PERSONALIZED.value}",
        headers=auth_headers,
    )
    assert status_resp.status_code == 200
    assert len(status_resp.json()["items"]) == 1

    # 3. Lead detail with timeline
    detail_resp = client.get(f"/api/v1/leads/{lead1.id}", headers=auth_headers)
    assert detail_resp.status_code == 200
    detail_json = detail_resp.json()
    assert detail_json["email"] == "developer@techco.com"
    assert "events" in detail_json
    assert "emails" in detail_json


def test_leads_manual_actions(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test manual lead actions (pause, resume, mark_won, skip_step, etc)."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=camp.id,
        email="operator_target@domain.com",
        status=LeadStatus.APPROVED.value,
    )
    session.add(lead)
    session.flush()

    msg1 = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Intro",
        body="Body 1",
        status=EmailMessageStatus.APPROVED.value,
    )
    msg2 = EmailMessage(
        lead_id=lead.id,
        step=2,
        subject="Re: Intro",
        body="Body 2",
        status=EmailMessageStatus.DRAFT.value,
    )
    session.add_all([msg1, msg2])
    session.commit()

    # 1. Pause lead
    p_resp = client.post(f"/api/v1/leads/{lead.id}/pause", headers=auth_headers)
    assert p_resp.status_code == 200
    assert p_resp.json()["status"] == LeadStatus.PAUSED.value

    # 2. Resume lead
    r_resp = client.post(f"/api/v1/leads/{lead.id}/resume", headers=auth_headers)
    assert r_resp.status_code == 200
    assert r_resp.json()["status"] == LeadStatus.APPROVED.value

    # 3. Skip step
    skip_resp = client.post(f"/api/v1/leads/{lead.id}/skip-step", headers=auth_headers)
    assert skip_resp.status_code == 200

    # 4. Mark replied
    rep_resp = client.post(f"/api/v1/leads/{lead.id}/mark-replied", headers=auth_headers)
    assert rep_resp.status_code == 200
    assert rep_resp.json()["status"] == LeadStatus.REPLIED.value

    # 5. Mark won
    won_resp = client.post(f"/api/v1/leads/{lead.id}/mark-won", headers=auth_headers)
    assert won_resp.status_code == 200
    assert won_resp.json()["status"] == LeadStatus.WON.value

    # 6. Mark not interested -> suppresses email
    unsub_resp = client.post(f"/api/v1/leads/{lead.id}/mark-not-interested", headers=auth_headers)
    assert unsub_resp.status_code == 200
    assert unsub_resp.json()["status"] == LeadStatus.UNSUBSCRIBED.value


# =============================================================================
# 4. Approval Queue Tests
# =============================================================================
def test_approval_queue_lifecycle(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test approval queue listing, approve, bulk approve, edit-and-approve, reject."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))

    lead = Lead(
        campaign_id=camp.id,
        email="review_lead@review.com",
        personal_note="I noticed your Postgres snapshot tool.",
        evidence_quote="Postgres snapshot tool",
        status=LeadStatus.PERSONALIZED.value,
    )
    session.add(lead)
    session.commit()

    # 1. Pending list
    pend_resp = client.get("/api/v1/approval/pending", headers=auth_headers)
    assert pend_resp.status_code == 200
    items = pend_resp.json()["items"]
    assert len(items) >= 1
    assert items[0]["evidence_quote"] == "Postgres snapshot tool"

    # 2. Edit and approve
    edit_resp = client.post(
        f"/api/v1/approval/{lead.id}/edit-and-approve",
        json={"new_note": "Saw your excellent database recovery features."},
        headers=auth_headers,
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["status"] in (LeadStatus.APPROVED.value, LeadStatus.IN_SEQUENCE.value)
    assert edit_resp.json()["personal_note"] == "Saw your excellent database recovery features."

    # 3. Reject non-existent returns 404
    bad_reject = client.post(
        "/api/v1/approval/9999/reject", json={"reason": "bad"}, headers=auth_headers
    )
    assert bad_reject.status_code == 404


# =============================================================================
# 5. Enrollment & Planning Tests
# =============================================================================
def test_enrollment_and_planning_endpoints(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test enrollment of approved leads and capacity preview."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))

    # Add approved lead
    lead = Lead(
        campaign_id=camp.id,
        email="enrolled@example.com",
        personal_note="Valid personal note.",
        status=LeadStatus.APPROVED.value,
    )
    session.add(lead)
    session.commit()

    # 1. Enroll
    enroll_resp = client.post(
        "/api/v1/enrollment/enroll",
        json={"campaign_id": camp.id},
        headers=auth_headers,
    )
    assert enroll_resp.status_code == 200
    assert enroll_resp.json()["enrolled_count"] >= 1

    # 2. Planning preview
    preview_resp = client.get(
        "/api/v1/planning/preview?target_date=2026-10-16",
        headers=auth_headers,
    )
    assert preview_resp.status_code == 200
    assert "remaining_capacity" in preview_resp.json()

    # 3. Capacity endpoint
    cap_resp = client.get("/api/v1/planning/capacity", headers=auth_headers)
    assert cap_resp.status_code == 200
    assert "daily_capacity" in cap_resp.json()


# =============================================================================
# 6. Emails, Outbox, and Inbound Replies
# =============================================================================
def test_emails_and_replies_endpoints(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test emails listing, outbox view, and replies handling."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))
    lead = Lead(
        campaign_id=camp.id, email="replier@client.com", status=LeadStatus.IN_SEQUENCE.value
    )
    session.add(lead)
    session.flush()

    outbox_email = OutboxEmail(
        to_email=lead.email,
        from_email="operator@test.com",
        subject="Intro email",
        body="Hello World",
        status="sent (dry-run)",
    )
    reply = Reply(
        lead_id=lead.id,
        from_email=lead.email,
        subject="Re: Intro email",
        body="Interested in chatting!",
        snippet="Interested in chatting!",
        classification="positive_hint",
        is_positive_hint=True,
    )
    session.add_all([outbox_email, reply])
    session.commit()

    # 1. Outbox list
    outbox_resp = client.get("/api/v1/emails/outbox", headers=auth_headers)
    assert outbox_resp.status_code == 200
    assert len(outbox_resp.json()["items"]) >= 1

    # 2. Replies list
    reply_resp = client.get("/api/v1/replies?is_positive_hint=true", headers=auth_headers)
    assert reply_resp.status_code == 200
    assert len(reply_resp.json()["items"]) >= 1

    # 3. Mark reply won
    won_resp = client.post(f"/api/v1/replies/{reply.id}/mark-won", headers=auth_headers)
    assert won_resp.status_code == 200


# =============================================================================
# 7. Templates & Sequence Step Management
# =============================================================================
def test_templates_sequence_and_preview(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test retrieving sequence YAML, updating YAML, and template preview."""
    session, _ = test_db_session
    camp = session.scalar(select(Campaign).limit(1))

    # 1. Get sequence
    seq_resp = client.get(f"/api/v1/templates/sequence?campaign_id={camp.id}", headers=auth_headers)
    assert seq_resp.status_code == 200
    assert "yaml_content" in seq_resp.json()

    # 2. Update sequence with invalid YAML (400)
    bad_yaml_resp = client.put(
        "/api/v1/templates/sequence",
        json={"campaign_id": camp.id, "yaml_content": "invalid: [yaml: broken"},
        headers=auth_headers,
    )
    assert bad_yaml_resp.status_code == 400

    # 3. Preview email template
    prev_resp = client.post(
        "/api/v1/templates/preview",
        json={
            "custom_subject": "Quick question for {{company}}",
            "custom_body": (
                "Hi {{first_name}}, {{personal_note}}. "
                "Could we chat next week?\n\nBest,\n{{sender_name}}"
            ),
        },
        headers=auth_headers,
    )
    assert prev_resp.status_code == 200
    assert "Cyberdyne" in prev_resp.json()["subject"]
    assert "linter_passed" in prev_resp.json()


# =============================================================================
# 8. Suppression List API
# =============================================================================
def test_suppression_crud_and_csv_io(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Test adding, listing, removing, importing, and exporting suppressions."""
    # 1. Add suppression
    add_resp = client.post(
        "/api/v1/suppression",
        json={"entry": "blockme@competitor.com", "reason": "competitor"},
        headers=auth_headers,
    )
    assert add_resp.status_code == 201
    assert add_resp.json()["email"] == "blockme@competitor.com"

    # 2. List suppression
    list_resp = client.get("/api/v1/suppression?search=competitor", headers=auth_headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()["items"]) >= 1

    # 3. Export CSV
    export_resp = client.get("/api/v1/suppression/export", headers=auth_headers)
    assert export_resp.status_code == 200
    assert "blockme@competitor.com" in export_resp.text

    # 4. Remove suppression
    del_resp = client.delete("/api/v1/suppression/blockme@competitor.com", headers=auth_headers)
    assert del_resp.status_code == 200

    # 5. Remove non-existent returns 404
    bad_del = client.delete("/api/v1/suppression/notfound@example.com", headers=auth_headers)
    assert bad_del.status_code == 404


# =============================================================================
# 9. Health & Safety Monitor and Resume
# =============================================================================
def test_safety_status_and_resume_flow(
    client: TestClient,
    test_db_session: tuple[Session, sessionmaker],
    auth_headers: dict[str, str],
) -> None:
    """Test safety status retrieval and kill-switch resumption validation."""
    session, _ = test_db_session

    # Engage killswitch manually
    trigger_killswitch(session, reason="High bounce rate detected")

    # 1. Check status reflects killswitch active
    status_resp = client.get("/api/v1/safety/status", headers=auth_headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["killswitch_active"] is True

    # 2. Resume without proper acknowledgement phrase rejected (400)
    bad_resume = client.post(
        "/api/v1/safety/resume",
        json={"acknowledgement": "yes resume please", "note": "Fixed bad MX records"},
        headers=auth_headers,
    )
    assert bad_resume.status_code == 400

    # 3. Resume with valid acknowledgement and note succeeds
    good_resume = client.post(
        "/api/v1/safety/resume",
        json={
            "acknowledgement": "I ACKNOWLEDGE AND RESUME",
            "note": "Re-verified domain DNS records and removed stale contacts",
        },
        headers=auth_headers,
    )
    assert good_resume.status_code == 200
    assert good_resume.json()["status"] == "resumed"

    # Status reflects disarmed killswitch
    status_after = client.get("/api/v1/safety/status", headers=auth_headers)
    assert status_after.json()["killswitch_active"] is False


# =============================================================================
# 10. Settings Validation & Bounds Enforcement
# =============================================================================
def test_settings_validation_and_hard_cap_bounds(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Verify settings validation: cap cannot exceed 30, relaxing safety requires confirm."""
    # 1. Get whitelisted settings
    get_resp = client.get("/api/v1/settings", headers=auth_headers)
    assert get_resp.status_code == 200
    data = get_resp.json()
    assert "HARD_MAX_PER_DAY" in data
    assert "SMTP_PASSWORD" not in data  # Never expose secrets!

    # 2. Setting cap above HARD_MAX_PER_DAY (30) is unconditionally rejected (400)
    exceed_resp = client.put(
        "/api/v1/settings",
        json={"settings": {"HARD_MAX_PER_DAY": 50}},
        headers=auth_headers,
    )
    assert exceed_resp.status_code == 400
    assert "exceeds" in exceed_resp.json()["detail"].lower()

    # 3. Lowering MIN_GAP_SECONDS below 120 without confirm rejected (400)
    gap_reject = client.put(
        "/api/v1/settings",
        json={"settings": {"MIN_GAP_SECONDS": 60}, "confirm": False},
        headers=auth_headers,
    )
    assert gap_reject.status_code == 400
    assert "confirm" in gap_reject.json()["detail"].lower()

    # 4. Lowering MIN_GAP_SECONDS with confirm=True succeeds
    gap_accept = client.put(
        "/api/v1/settings",
        json={"settings": {"MIN_GAP_SECONDS": 90}, "confirm": True},
        headers=auth_headers,
    )
    assert gap_accept.status_code == 200
    assert gap_accept.json()["settings"]["MIN_GAP_SECONDS"] == 90


# =============================================================================
# 11. Server-Sent Events (SSE) Live Updates
# =============================================================================
def test_sse_delivers_live_events(
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Verify SSE endpoint streams connection handshake and live broadcast events."""
    # Trigger broadcast event in memory
    broadcast_live_event("reply", {"id": 101, "classification": "positive_hint"})

    # Connect to SSE endpoint with max_events=2 for immediate deterministic closure
    resp = client.get(
        "/api/v1/events?max_events=2",
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    body = resp.text
    assert "event: connected" in body
    assert "event: reply" in body
    assert "positive_hint" in body


def test_settings_test_email_endpoint(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch,
) -> None:
    """Verify test-email endpoint calls SMTP sender and handles success/error."""
    from unittest.mock import MagicMock

    from app.services.sender import SendResult, SmtpSender

    mock_send = MagicMock(return_value=SendResult(success=True, message_id="<test-id@example.com>"))
    monkeypatch.setattr(SmtpSender, "send", mock_send)

    resp = client.post(
        "/api/v1/settings/test-email",
        json={"to_email": "tester@example.com", "smtp_host": "smtp.gmail.com"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["recipient"] == "tester@example.com"
    assert mock_send.called

