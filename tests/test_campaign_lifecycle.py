"""Tests for campaign lifecycle: automatic completion and campaign deletion."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_clock, get_db
from app.core.clock import FakeClock
from app.core.config import get_settings
from app.core.db import Base
from app.main import create_app
from app.models.campaign import Campaign, CampaignStatus
from app.models.email_message import EmailMessage, EmailMessageStatus
from app.models.lead import Lead, LeadStatus, RiskLevel
from app.models.sequence import SequenceStep
from app.services.sequencer import check_and_update_completed_campaigns

TEST_TOKEN = "test-token-123"


@pytest.fixture
def test_env():
    settings = get_settings()
    settings.API_TOKEN = SecretStr(TEST_TOKEN)
    settings.LIVE_MODE = False

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    sm = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    clock = FakeClock(datetime(2026, 10, 14, 12, 0, 0, tzinfo=UTC))

    app = create_app()

    def override_db():
        with sm() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_clock] = lambda: clock

    client = TestClient(app)
    return client, sm, clock


def test_delete_campaign_cascades(test_env):
    client, sm, clock = test_env
    headers = {"X-API-Token": TEST_TOKEN}

    with sm() as db:
        camp = Campaign(name="To Delete", niche="Tech", status=CampaignStatus.ACTIVE.value)
        db.add(camp)
        db.commit()
        db.refresh(camp)
        cid = camp.id

        # Add sequence step and lead
        step = SequenceStep(
            campaign_id=cid,
            step=1,
            delay_days=0,
            subjects=["Hello {{first_name}}"],
            bodies=["World"],
        )
        db.add(step)
        lead = Lead(
            campaign_id=cid,
            email="lead@example.com",
            first_name="John",
            status=LeadStatus.COMPLETED.value,
            risk_level=RiskLevel.OK.value,
        )
        db.add(lead)
        db.commit()

    # Call DELETE /api/v1/campaigns/{cid}
    res = client.delete(f"/api/v1/campaigns/{cid}", headers=headers)
    assert res.status_code == 200
    assert res.json()["status"] == "deleted"

    # Verify campaign, step, and lead are removed
    with sm() as db:
        assert db.get(Campaign, cid) is None
        steps = (
            db.execute(select(SequenceStep).where(SequenceStep.campaign_id == cid)).scalars().all()
        )
        assert len(steps) == 0
        leads = db.execute(select(Lead).where(Lead.campaign_id == cid)).scalars().all()
        assert len(leads) == 0


def test_manual_complete_campaign(test_env):
    client, sm, clock = test_env
    headers = {"X-API-Token": TEST_TOKEN}

    with sm() as db:
        camp = Campaign(name="Complete Me", niche="Tech", status=CampaignStatus.ACTIVE.value)
        db.add(camp)
        db.commit()
        db.refresh(camp)
        cid = camp.id

    res = client.post(f"/api/v1/campaigns/{cid}/complete", headers=headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == CampaignStatus.COMPLETED.value

    with sm() as db:
        camp = db.get(Campaign, cid)
        assert camp.status == CampaignStatus.COMPLETED.value


def test_automatic_campaign_completion(test_env):
    _, sm, clock = test_env

    with sm() as db:
        camp = Campaign(name="Auto Complete Camp", niche="Tech", status=CampaignStatus.ACTIVE.value)
        db.add(camp)
        db.commit()
        db.refresh(camp)
        cid = camp.id

        # Lead 1: completed
        l1 = Lead(
            campaign_id=cid,
            email="l1@example.com",
            status=LeadStatus.COMPLETED.value,
            risk_level=RiskLevel.OK.value,
        )
        # Lead 2: replied
        l2 = Lead(
            campaign_id=cid,
            email="l2@example.com",
            status=LeadStatus.REPLIED.value,
            risk_level=RiskLevel.OK.value,
        )
        # Lead 3: still in_sequence
        l3 = Lead(
            campaign_id=cid,
            email="l3@example.com",
            status=LeadStatus.IN_SEQUENCE.value,
            risk_level=RiskLevel.OK.value,
        )
        db.add_all([l1, l2, l3])
        db.commit()

        # Check: should NOT complete yet because l3 is in_sequence
        completed = check_and_update_completed_campaigns(db, clock, campaign_id=cid)
        assert len(completed) == 0
        db.refresh(camp)
        assert camp.status == CampaignStatus.ACTIVE.value

        # Now mark l3 completed
        l3.status = LeadStatus.COMPLETED.value
        db.commit()

        # Check: should complete now!
        completed = check_and_update_completed_campaigns(db, clock, campaign_id=cid)
        assert cid in [c.id for c in completed]
        db.refresh(camp)
        assert camp.status == CampaignStatus.COMPLETED.value


def test_campaign_waits_for_all_followups_before_completing(test_env):
    """Verify that a campaign does NOT complete if follow-up emails are still pending/draft,
    and only completes once the final follow-up step is sent."""
    _, sm, clock = test_env

    with sm() as db:
        camp = Campaign(
            name="Followup Check Camp", niche="Tech", status=CampaignStatus.ACTIVE.value
        )
        db.add(camp)
        db.commit()
        db.refresh(camp)
        cid = camp.id

        # Add sequence steps (Step 1 cold, Step 2 follow-up, Step 3 follow-up)
        for s in (1, 2, 3):
            db.add(
                SequenceStep(
                    campaign_id=cid,
                    step=s,
                    delay_days=2 * (s - 1),
                    subjects=[f"Step {s} subject"],
                    bodies=[f"Step {s} body"],
                )
            )

        lead = Lead(
            campaign_id=cid,
            email="lead_followup@example.com",
            status=LeadStatus.IN_SEQUENCE.value,
            risk_level=RiskLevel.OK.value,
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)

        # Step 1 is SENT
        msg1 = EmailMessage(
            lead_id=lead.id,
            step=1,
            subject="Step 1",
            body="Body 1",
            status=EmailMessageStatus.SENT.value,
            sent_at=clock.now(),
        )
        # Step 2 is APPROVED (pending follow-up)
        msg2 = EmailMessage(
            lead_id=lead.id,
            step=2,
            subject="Step 2",
            body="Body 2",
            status=EmailMessageStatus.APPROVED.value,
        )
        # Step 3 is DRAFT (future follow-up)
        msg3 = EmailMessage(
            lead_id=lead.id,
            step=3,
            subject="Step 3",
            body="Body 3",
            status=EmailMessageStatus.DRAFT.value,
        )
        db.add_all([msg1, msg2, msg3])
        db.commit()

        # Check 1: Step 1 was sent, but Step 2 and Step 3 follow-ups are NOT sent yet.
        # Campaign MUST NOT be completed!
        completed = check_and_update_completed_campaigns(db, clock, campaign_id=cid)
        assert len(completed) == 0
        db.refresh(camp)
        assert camp.status == CampaignStatus.ACTIVE.value

        # Check 2: Step 2 (Follow-up 1) is sent, but Step 3 (Follow-up 2) is still DRAFT
        msg2.status = EmailMessageStatus.SENT.value
        msg2.sent_at = clock.now()
        db.commit()

        completed = check_and_update_completed_campaigns(db, clock, campaign_id=cid)
        assert len(completed) == 0
        db.refresh(camp)
        assert camp.status == CampaignStatus.ACTIVE.value

        # Check 3: Final follow-up (Step 3) is finally sent!
        msg3.status = EmailMessageStatus.SENT.value
        msg3.sent_at = clock.now()
        db.commit()

        # Now all follow-ups have been sent!
        completed = check_and_update_completed_campaigns(db, clock, campaign_id=cid)
        assert cid in [c.id for c in completed]
        db.refresh(camp)
        assert camp.status == CampaignStatus.COMPLETED.value
        db.refresh(lead)
        assert lead.status == LeadStatus.COMPLETED.value
