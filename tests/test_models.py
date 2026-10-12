"""Unit tests for SQLAlchemy models and table relationships."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.clock import FakeClock
from app.core.db import Base
from app.models import (
    Campaign,
    CampaignStatus,
    EmailMessage,
    EmailMessageStatus,
    Event,
    EventType,
    Lead,
    LeadStatus,
    Setting,
    Suppression,
)


@pytest.fixture
def memory_db() -> Session:
    """Provide an in-memory SQLite database session with all tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


def test_campaign_and_lead_relationship(memory_db: Session, fake_clock: FakeClock) -> None:
    """Verify campaign creation, lead creation, and relationship cascade."""
    now = fake_clock.now()
    campaign = Campaign(name="Alpha Outreach", status=CampaignStatus.ACTIVE.value, created_at=now)
    memory_db.add(campaign)
    memory_db.commit()

    lead = Lead(
        campaign_id=campaign.id,
        email="test@example.com",
        first_name="Jane",
        last_name="Doe",
        status=LeadStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    memory_db.add(lead)
    memory_db.commit()

    assert lead.id is not None
    assert lead.campaign.name == "Alpha Outreach"
    assert len(campaign.leads) == 1


def test_email_message_and_event(memory_db: Session, fake_clock: FakeClock) -> None:
    """Verify email messages and event trail persistence."""
    now = fake_clock.now()
    campaign = Campaign(name="Beta Outreach", created_at=now)
    memory_db.add(campaign)
    memory_db.commit()

    lead = Lead(
        campaign_id=campaign.id,
        email="lead@example.com",
        status=LeadStatus.NEW.value,
        created_at=now,
        updated_at=now,
    )
    memory_db.add(lead)
    memory_db.commit()

    msg = EmailMessage(
        lead_id=lead.id,
        step=1,
        subject="Quick question",
        body="Hi Jane, would love to connect.",
        status=EmailMessageStatus.DRAFT.value,
    )
    event = Event(
        lead_id=lead.id,
        type=EventType.IMPORTED.value,
        payload={"source": "test"},
        created_at=now,
    )
    memory_db.add_all([msg, event])
    memory_db.commit()

    saved_msg = memory_db.scalar(select(EmailMessage).where(EmailMessage.lead_id == lead.id))
    assert saved_msg is not None
    assert saved_msg.subject == "Quick question"

    saved_event = memory_db.scalar(select(Event).where(Event.lead_id == lead.id))
    assert saved_event is not None
    assert saved_event.type == EventType.IMPORTED.value


def test_suppression_and_setting(memory_db: Session, fake_clock: FakeClock) -> None:
    """Verify suppression list entry and setting key-value storage."""
    now = fake_clock.now()
    suppression = Suppression(
        email="blocked@domain.com",
        reason="manual_opt_out",
        created_at=now,
    )
    setting = Setting(key="daily_quota", value="50")
    memory_db.add_all([suppression, setting])
    memory_db.commit()

    saved_suppression = memory_db.scalar(
        select(Suppression).where(Suppression.email == "blocked@domain.com")
    )
    assert saved_suppression is not None
    assert saved_suppression.reason == "manual_opt_out"

    saved_setting = memory_db.scalar(select(Setting).where(Setting.key == "daily_quota"))
    assert saved_setting is not None
    assert saved_setting.value == "50"
