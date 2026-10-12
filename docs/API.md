# ColdReach Outreach Automation API

**Version**: 1.0.0  
**Base URL**: `http://127.0.0.1:8000/api/v1`  
**Allowed CORS Origin**: `http://localhost:5173` only  

## Security & Authentication

Every endpoint under `/api/v1` requires authentication via the HTTP header:
```http
X-API-Token: <your_secret_token>
```
Generate a random token in your `.env` file via:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## API Endpoints Summary

| Method | Path | Summary | Tags |
|:---|:---|:---|:---|
| `GET` | `/api/health` | Health Check | Health |
| `POST` | `/api/v1/approval/approve-many` | Bulk Approve Drafts | Approval Queue |
| `GET` | `/api/v1/approval/pending` | Get Pending Approvals | Approval Queue |
| `POST` | `/api/v1/approval/{lead_id}/approve` | Approve Draft | Approval Queue |
| `POST` | `/api/v1/approval/{lead_id}/edit-and-approve` | Edit And Approve Draft | Approval Queue |
| `POST` | `/api/v1/approval/{lead_id}/regenerate` | Regenerate Draft | Approval Queue |
| `POST` | `/api/v1/approval/{lead_id}/reject` | Reject Draft | Approval Queue |
| `GET` | `/api/v1/campaigns` | List Campaigns | Campaigns |
| `POST` | `/api/v1/campaigns` | Create Campaign | Campaigns |
| `GET` | `/api/v1/campaigns/{campaign_id}` | Get Campaign | Campaigns |
| `PATCH` | `/api/v1/campaigns/{campaign_id}` | Update Campaign | Campaigns |
| `POST` | `/api/v1/campaigns/{campaign_id}/activate` | Activate Campaign | Campaigns |
| `POST` | `/api/v1/campaigns/{campaign_id}/pause` | Pause Campaign | Campaigns |
| `POST` | `/api/v1/campaigns/{campaign_id}/resume` | Resume Campaign | Campaigns |
| `GET` | `/api/v1/emails` | List Emails | Emails |
| `GET` | `/api/v1/emails/outbox` | List Outbox Emails | Emails |
| `GET` | `/api/v1/emails/{email_id}` | Get Email Message | Emails |
| `POST` | `/api/v1/enrollment/enroll` | Enroll Approved Leads | Enrollment |
| `GET` | `/api/v1/events` | Stream Live Events | Live Updates |
| `GET` | `/api/v1/leads` | List Leads | Leads |
| `POST` | `/api/v1/leads/upload` | Upload Leads Csv | Leads |
| `POST` | `/api/v1/leads/validate` | Bulk Validate Leads | Leads |
| `GET` | `/api/v1/leads/{lead_id}` | Get Lead Detail | Leads |
| `POST` | `/api/v1/leads/{lead_id}/mark-not-interested` | Mark Lead Not Interested | Leads |
| `POST` | `/api/v1/leads/{lead_id}/mark-replied` | Mark Lead Replied | Leads |
| `POST` | `/api/v1/leads/{lead_id}/mark-won` | Mark Lead Won | Leads |
| `POST` | `/api/v1/leads/{lead_id}/pause` | Pause Lead | Leads |
| `POST` | `/api/v1/leads/{lead_id}/resume` | Resume Lead | Leads |
| `POST` | `/api/v1/leads/{lead_id}/skip-step` | Skip Current Step | Leads |
| `GET` | `/api/v1/planning/capacity` | Get Capacity | Planning |
| `GET` | `/api/v1/planning/preview` | Preview Plan | Planning |
| `GET` | `/api/v1/replies` | List Replies | Replies |
| `POST` | `/api/v1/replies/{reply_id}/handle` | Handle Reply | Replies |
| `POST` | `/api/v1/replies/{reply_id}/mark-won` | Mark Reply Won | Replies |
| `POST` | `/api/v1/safety/resume` | Resume Killswitch | Health & Safety |
| `GET` | `/api/v1/safety/status` | Get Safety Status | Health & Safety |
| `GET` | `/api/v1/settings` | Get Whitelisted Settings | Settings |
| `PUT` | `/api/v1/settings` | Update Whitelisted Settings | Settings |
| `GET` | `/api/v1/stats/bounce-rate` | Get Bounce Rate Stats | Stats |
| `GET` | `/api/v1/stats/capacity` | Get Today Capacity Stats | Stats |
| `GET` | `/api/v1/stats/funnel` | Get Funnel Counts | Stats |
| `GET` | `/api/v1/stats/health` | Get Overall Health Stats | Stats |
| `GET` | `/api/v1/stats/templates` | Get Template Variant Stats | Stats |
| `GET` | `/api/v1/stats/timeseries` | Get Daily Timeseries | Stats |
| `GET` | `/api/v1/suppression` | List Suppression | Suppression |
| `POST` | `/api/v1/suppression` | Add Suppression Entry | Suppression |
| `GET` | `/api/v1/suppression/export` | Export Suppression Csv | Suppression |
| `POST` | `/api/v1/suppression/import` | Import Suppression File | Suppression |
| `DELETE` | `/api/v1/suppression/{entry}` | Remove Suppression Entry | Suppression |
| `POST` | `/api/v1/templates/preview` | Preview Email Template | Templates |
| `GET` | `/api/v1/templates/sequence` | Get Sequence Steps | Templates |
| `PUT` | `/api/v1/templates/sequence` | Update Sequence Steps | Templates |

---

## Detailed Endpoint Specifications

### `GET` /api/health
**Category**: Health  
**Summary**: Health Check  

Health check endpoint exposing runtime safety status.


**Responses**:
- **`200`**: Successful Response

---

### `POST` /api/v1/approval/approve-many
**Category**: Approval Queue  
**Summary**: Bulk Approve Drafts  

Batch approve multiple personalized lead drafts.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/approval/pending
**Category**: Approval Queue  
**Summary**: Get Pending Approvals  

List pending AI-personalized drafts with evidence quote for operator review.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | query | `string` | No |  |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/approval/{lead_id}/approve
**Category**: Approval Queue  
**Summary**: Approve Draft  

Approve a personalized lead draft for sequence enrollment.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/approval/{lead_id}/edit-and-approve
**Category**: Approval Queue  
**Summary**: Edit And Approve Draft  

Edit personal note and immediately approve lead.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/approval/{lead_id}/regenerate
**Category**: Approval Queue  
**Summary**: Regenerate Draft  

Trigger AI re-generation of personalization for lead.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/approval/{lead_id}/reject
**Category**: Approval Queue  
**Summary**: Reject Draft  

Reject a draft and mark lead needs_manual_note.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/campaigns
**Category**: Campaigns  
**Summary**: List Campaigns  

Retrieve paginated list of campaigns with lead counts.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `status` | query | `string` | No | Filter by campaign status |
| `sort_by` | query | `string` | No | Field to sort by |
| `sort_order` | query | `string` | No | asc or desc |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/campaigns
**Category**: Campaigns  
**Summary**: Create Campaign  

Create a new campaign and auto-seed default sequence steps.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`201`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/campaigns/{campaign_id}
**Category**: Campaigns  
**Summary**: Get Campaign  

Get single campaign details and summary counts.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `PATCH` /api/v1/campaigns/{campaign_id}
**Category**: Campaigns  
**Summary**: Update Campaign  

Update campaign metadata.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/campaigns/{campaign_id}/activate
**Category**: Campaigns  
**Summary**: Activate Campaign  

Activate a draft or paused campaign.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/campaigns/{campaign_id}/pause
**Category**: Campaigns  
**Summary**: Pause Campaign  

Pause an active campaign.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/campaigns/{campaign_id}/resume
**Category**: Campaigns  
**Summary**: Resume Campaign  

Resume a paused campaign.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/emails
**Category**: Emails  
**Summary**: List Emails  

List outbound email messages with status and campaign filtering.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `status` | query | `string` | No | Filter by message status |
| `campaign_id` | query | `string` | No | Filter by campaign ID |
| `lead_id` | query | `string` | No | Filter by lead ID |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/emails/outbox
**Category**: Emails  
**Summary**: List Outbox Emails  

List rendered outbox messages persisted during dry-run operation.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/emails/{email_id}
**Category**: Emails  
**Summary**: Get Email Message  

Retrieve single email message record.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `email_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/enrollment/enroll
**Category**: Enrollment  
**Summary**: Enroll Approved Leads  

Enroll all approved leads of a campaign into the outreach sequence.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/events
**Category**: Live Updates  
**Summary**: Stream Live Events  

Stream live events (new replies, killswitch status, alerts) via Server-Sent Events.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `last_event_id` | query | `integer` | No | Resume stream from event ID |
| `max_events` | query | `string` | No | Max events before closing (for tests) |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/leads
**Category**: Leads  
**Summary**: List Leads  

List leads with multi-field filtering and pagination.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | query | `string` | No |  |
| `status` | query | `string` | No |  |
| `risk_level` | query | `string` | No |  |
| `country` | query | `string` | No |  |
| `search` | query | `string` | No | Search text across email, name, company |
| `sort_by` | query | `string` | No |  |
| `sort_order` | query | `string` | No |  |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/upload
**Category**: Leads  
**Summary**: Upload Leads Csv  

Upload multipart CSV lead file and run import service.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/validate
**Category**: Leads  
**Summary**: Bulk Validate Leads  

Bulk validate unverified 'new' leads using email validator service.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_name` | query | `string` | No |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/leads/{lead_id}
**Category**: Leads  
**Summary**: Get Lead Detail  

Retrieve full lead profile with timeline events, emails, and replies.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/mark-not-interested
**Category**: Leads  
**Summary**: Mark Lead Not Interested  

Mark lead as not interested, unsubscribe, and add to global suppression.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/mark-replied
**Category**: Leads  
**Summary**: Mark Lead Replied  

Mark lead as replied and cancel any remaining sequence emails.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/mark-won
**Category**: Leads  
**Summary**: Mark Lead Won  

Mark lead as won deal.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/pause
**Category**: Leads  
**Summary**: Pause Lead  

Pause outreach for a specific lead and cancel draft/scheduled messages.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/resume
**Category**: Leads  
**Summary**: Resume Lead  

Resume a paused lead.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/leads/{lead_id}/skip-step
**Category**: Leads  
**Summary**: Skip Current Step  

Skip the current pending email message step for this lead.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `lead_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/planning/capacity
**Category**: Planning  
**Summary**: Get Capacity  

Get total and remaining outbound dispatch capacity for a given date.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `check_date` | query | `string` | No | Date in YYYY-MM-DD format |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/planning/preview
**Category**: Planning  
**Summary**: Preview Plan  

Preview outbound emails scheduled or due to be sent on the specified target date.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `target_date` | query | `string` | No | Date in YYYY-MM-DD format (defaults to tomorrow) |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/replies
**Category**: Replies  
**Summary**: List Replies  

List captured inbound replies with classifications and positive sentiment flags.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `classification` | query | `string` | No | Filter by classification |
| `is_positive_hint` | query | `string` | No | Filter positive hints |
| `lead_id` | query | `string` | No | Filter by lead ID |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/replies/{reply_id}/handle
**Category**: Replies  
**Summary**: Handle Reply  

Mark inbound reply as reviewed/handled by operator.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `reply_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/replies/{reply_id}/mark-won
**Category**: Replies  
**Summary**: Mark Reply Won  

Mark lead associated with this reply as won customer.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `reply_id` | path | `integer` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/safety/resume
**Category**: Health & Safety  
**Summary**: Resume Killswitch  

Resume outbound sending from emergency kill-switch state.

Requires explicit user acknowledgement phrase ('I ACKNOWLEDGE',
'I ACKNOWLEDGE AND RESUME', or 'I UNDERSTAND AND ACCEPT RISK') and
a descriptive explanation note (>= 5 chars). Resumption halves dispatch
capacity for the following 3 days.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/safety/status
**Category**: Health & Safety  
**Summary**: Get Safety Status  

Retrieve current kill-switch state, deliverability health metrics, and safety alerts.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/settings
**Category**: Settings  
**Summary**: Get Whitelisted Settings  

Retrieve current values for all whitelisted non-secret system settings.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `PUT` /api/v1/settings
**Category**: Settings  
**Summary**: Update Whitelisted Settings  

Update whitelisted settings with strict bounds and safety validation.

Rules:
- No daily send cap may exceed HARD_MAX_PER_DAY (30).
- Lowering safety throttles below defaults (e.g. MIN_GAP_SECONDS < 120 or
  BOUNCE_RATE_STOP_THRESHOLD_PERCENT > 2.0) requires explicit `confirm=True`.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/bounce-rate
**Category**: Stats  
**Summary**: Get Bounce Rate Stats  

Retrieve current short-term and 7-day bounce rates.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/capacity
**Category**: Stats  
**Summary**: Get Today Capacity Stats  

Retrieve today's dispatch cap usage.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/funnel
**Category**: Stats  
**Summary**: Get Funnel Counts  

Retrieve funnel stage counts (imported -> valid -> approved -> sent -> replied -> won).


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/health
**Category**: Stats  
**Summary**: Get Overall Health Stats  

Calculate overall deliverability health score (0-100).


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/templates
**Category**: Stats  
**Summary**: Get Template Variant Stats  

Calculate send volumes and reply conversion rates per sequence variant.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/stats/timeseries
**Category**: Stats  
**Summary**: Get Daily Timeseries  

Retrieve daily sent, reply, and bounce counts over the last N days.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `days` | query | `integer` | No |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/suppression
**Category**: Suppression  
**Summary**: List Suppression  

Retrieve paginated global suppression list.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `search` | query | `string` | No | Filter by email or domain substring |
| `is_domain` | query | `string` | No | Filter by domain flag |
| `page` | query | `integer` | No | Page number (1-based) |
| `page_size` | query | `integer` | No | Items per page |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/suppression
**Category**: Suppression  
**Summary**: Add Suppression Entry  

Add email address or domain to global suppression.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`201`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/suppression/export
**Category**: Suppression  
**Summary**: Export Suppression Csv  

Export entire suppression list as CSV file.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/suppression/import
**Category**: Suppression  
**Summary**: Import Suppression File  

Batch import suppressed emails and domains from CSV or plain-text file.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `default_reason` | query | `string` | No |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `DELETE` /api/v1/suppression/{entry}
**Category**: Suppression  
**Summary**: Remove Suppression Entry  

Remove email address or domain from global suppression.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `entry` | path | `string` | Yes |  |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `POST` /api/v1/templates/preview
**Category**: Templates  
**Summary**: Preview Email Template  

Render an outreach email step and evaluate with spam linter.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `GET` /api/v1/templates/sequence
**Category**: Templates  
**Summary**: Get Sequence Steps  

Retrieve sequence step definitions and raw YAML representation for a campaign.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `campaign_id` | query | `integer` | Yes | Campaign ID |
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---

### `PUT` /api/v1/templates/sequence
**Category**: Templates  
**Summary**: Update Sequence Steps  

Update and re-validate sequence step definitions from YAML string.


**Parameters**:
| Name | In | Type | Required | Description |
|:---|:---|:---|:---|:---|
| `X-API-Token` | header | `string` | No |  |

**Responses**:
- **`200`**: Successful Response
- **`422`**: Validation Error

---
