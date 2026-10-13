"""Pydantic schemas for ColdReach REST API request and response validation."""

from datetime import datetime
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic envelope for paginated collections."""

    items: list[T]
    total: int
    page: int
    page_size: int
    pages: int


# =============================================================================
# Campaigns
# =============================================================================
class CampaignCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    niche: str | None = Field(default=None, max_length=255)


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    niche: str | None = Field(default=None, max_length=255)


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    niche: str | None
    status: str
    created_at: datetime
    leads_count: int = 0
    active_leads_count: int = 0


# =============================================================================
# Leads
# =============================================================================
class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    email: str
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    website: str | None = None
    title: str | None = None
    country: str | None = None
    timezone: str | None = None
    status: str
    risk_level: str | None = None
    validation_reason: str | None = None
    personal_note: str | None = None
    evidence_quote: str | None = None
    created_at: datetime
    updated_at: datetime


class LeadTimelineEvent(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    payload: dict[str, Any] | None = None
    created_at: datetime


class LeadEmailSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step: int
    subject: str
    status: str
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    error: str | None = None


class LeadReplySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    from_email: str
    subject: str
    snippet: str
    classification: str
    is_positive_hint: bool
    created_at: datetime


class LeadDetailResponse(LeadResponse):
    campaign_name: str | None = None
    events: list[LeadTimelineEvent] = []
    emails: list[LeadEmailSummary] = []
    replies: list[LeadReplySummary] = []


class LeadBulkValidateResponse(BaseModel):
    total_checked: int
    valid_count: int
    invalid_count: int
    risky_count: int


class PersonalizeResponse(BaseModel):
    total: int
    personalized: int
    preserved: int
    needs_manual_note: int
    message: str


# =============================================================================
# Approval Queue
# =============================================================================
class PendingApprovalItem(BaseModel):
    lead_id: int
    campaign_id: int
    campaign_name: str | None = None
    email: str
    company: str | None = None
    website: str | None = None
    personal_note: str | None = None
    evidence_quote: str | None = None


class ApproveManyRequest(BaseModel):
    lead_ids: list[int] = Field(..., min_length=1)


class EditAndApproveRequest(BaseModel):
    new_note: str = Field(..., min_length=1, max_length=500)


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


# =============================================================================
# Enrollment & Planning
# =============================================================================
class EnrollRequest(BaseModel):
    campaign_id: int


class EnrollResponse(BaseModel):
    campaign_id: int
    enrolled_count: int
    lead_ids: list[int]


class PlannedEmailItem(BaseModel):
    email_id: int
    lead_id: int
    email: str
    company: str | None = None
    step: int
    subject: str
    scheduled_at: datetime | None = None


class PlanningPreviewResponse(BaseModel):
    target_date: str
    remaining_capacity: int
    planned_emails: list[PlannedEmailItem]


class CapacityResponse(BaseModel):
    date: str
    daily_capacity: int
    sent_today: int
    remaining_capacity: int
    is_cap_halved: bool = False


# =============================================================================
# Emails & Outbox
# =============================================================================
class EmailMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int
    step: int
    subject: str
    body: str
    status: str
    scheduled_at: datetime | None = None
    sent_at: datetime | None = None
    message_id: str | None = None
    error: str | None = None
    retry_count: int = 0


class OutboxEmailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email_message_id: int | None = None
    to_email: str
    from_email: str
    subject: str
    body: str
    headers: dict[str, Any] | None = None
    status: str
    created_at: datetime


# =============================================================================
# Replies
# =============================================================================
class ReplyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    lead_id: int | None = None
    email_message_id: int | None = None
    from_email: str
    to_email: str | None = None
    subject: str
    body: str
    snippet: str
    classification: str
    is_positive_hint: bool
    created_at: datetime


# =============================================================================
# Templates & Sequences
# =============================================================================
class SequenceStepResponse(BaseModel):
    step: int
    delay_days: int
    send_as_reply: bool
    subjects: list[str]
    bodies: list[str]


class SequenceYamlRequest(BaseModel):
    campaign_id: int
    yaml_content: str = Field(..., min_length=1)


class TemplatePreviewRequest(BaseModel):
    lead_id: int | None = None
    campaign_id: int | None = None
    step: int = 1
    custom_subject: str | None = None
    custom_body: str | None = None


class TemplatePreviewResponse(BaseModel):
    subject: str
    body: str
    linter_passed: bool
    linter_score: int
    linter_failures: list[str]
    linter_warnings: list[str]
    is_ready: bool
    error: str | None = None


# =============================================================================
# Suppression
# =============================================================================
class SuppressionCreate(BaseModel):
    entry: str = Field(..., min_length=3, max_length=320)
    reason: str | None = Field(default="manual suppression", max_length=255)


class SuppressionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    reason: str | None = None
    is_domain: bool = False
    created_at: datetime


# =============================================================================
# Stats & Safety
# =============================================================================
class DailyStatsItem(BaseModel):
    date: str
    sent: int = 0
    replies: int = 0
    bounces: int = 0


class FunnelStatsResponse(BaseModel):
    imported: int
    valid: int
    approved: int
    sent: int
    replied: int
    won: int


class VariantStatsItem(BaseModel):
    step: int
    subject_variant_index: int | None
    body_variant_index: int | None
    sent_count: int
    replied_count: int
    reply_rate_percent: float


class SafetyAlertItem(BaseModel):
    id: int
    type: str
    message: str | None = None
    created_at: datetime


class SafetyStatusResponse(BaseModel):
    killswitch_active: bool
    health_score: int
    severity: str
    bounce_rate_100: float
    bounce_rate_7d: float
    failure_rate: float
    complaint_signals_7d: int
    unsubscribe_rate_100: float
    auth_or_block_errors: int
    sample_size_ok: bool
    reasons: list[str] = []
    recent_alerts: list[SafetyAlertItem] = []


class SafetyResumeRequest(BaseModel):
    acknowledgement: str = Field(..., min_length=5)
    note: str = Field(..., min_length=5)


# =============================================================================
# Settings
# =============================================================================
class SettingsUpdateRequest(BaseModel):
    settings: dict[str, Any]
    confirm: bool = False
