import type {
  Campaign,
  CampaignCreate,
  CampaignUpdate,
  CampaignScheduleItem,
  Lead,
  LeadTimeline,
  ImportReport,
  ApprovalQueueItem,
  EnrollmentResponse,
  PlanningPreview,
  CapacityResponse,
  EmailMessage,
  OutboxEmail,
  Reply,
  TemplatePreviewRequest,
  TemplatePreviewResponse,
  SuppressionEntry,
  FunnelStats,
  TimeseriesPoint,
  TemplateVariantStat,
  BounceRateStats,
  SafetyStatus,
  SafetyResumeRequest,
  WhitelistedSettings,
  PaginatedResult,
  Mailbox,
  MailboxCreate,
  ReplyInbox,
} from './types'

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
const API_TOKEN = import.meta.env.VITE_API_TOKEN || ''

class ApiError extends Error {
  status: number
  detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const url = `${API_BASE_URL}${path}`
  const headers = new Headers(options.headers || {})

  if (API_TOKEN) {
    headers.set('X-API-Token', API_TOKEN)
  }

  // Set default Accept header
  if (!headers.has('Accept')) {
    headers.set('Accept', 'application/json')
  }

  // Auto set Content-Type to json if body is stringified and not FormData
  if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(url, {
    ...options,
    headers,
  })

  if (!response.ok) {
    let errorDetail = `Request failed with status ${response.status}`
    try {
      const errJson = await response.json()
      if (errJson && errJson.detail) {
        errorDetail = typeof errJson.detail === 'string' ? errJson.detail : JSON.stringify(errJson.detail)
      }
    } catch {
      // response is not json
    }
    throw new ApiError(response.status, errorDetail)
  }

  if (response.status === 204) {
    return {} as T
  }

  return response.json() as Promise<T>
}

function buildQuery(params?: Record<string, unknown>): string {
  if (!params) return ''
  const searchParams = new URLSearchParams()
  for (const [key, val] of Object.entries(params)) {
    if (val !== undefined && val !== null && val !== '') {
      searchParams.set(key, String(val))
    }
  }
  const queryStr = searchParams.toString()
  return queryStr ? `?${queryStr}` : ''
}

export const api = {
  // Campaign Endpoints
  campaigns: {
    list: (params?: { page?: number; page_size?: number; status?: string }) =>
      request<PaginatedResult<Campaign>>(`/api/v1/campaigns${buildQuery(params)}`),
    create: (data: CampaignCreate) =>
      request<Campaign>('/api/v1/campaigns', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    get: (id: number) =>
      request<Campaign>(`/api/v1/campaigns/${id}`),
    update: (id: number, data: CampaignUpdate) =>
      request<Campaign>(`/api/v1/campaigns/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    pause: (id: number) =>
      request<Campaign>(`/api/v1/campaigns/${id}/pause`, { method: 'POST' }),
    resume: (id: number) =>
      request<Campaign>(`/api/v1/campaigns/${id}/resume`, { method: 'POST' }),
    activate: (id: number) =>
      request<Campaign>(`/api/v1/campaigns/${id}/activate`, { method: 'POST' }),
    schedule: (id: number) =>
      request<CampaignScheduleItem[]>(`/api/v1/campaigns/${id}/schedule`),
  },

  // Leads Endpoints
  leads: {
    list: (params?: {
      page?: number
      page_size?: number
      campaign_id?: number
      status?: string
      risk_level?: string
      country?: string
      search?: string
      sort_by?: string
      sort_order?: string
    }) =>
      request<PaginatedResult<Lead>>(`/api/v1/leads${buildQuery(params)}`),
    get: (id: number) =>
      request<LeadTimeline>(`/api/v1/leads/${id}`),
    uploadCsv: (file: File, campaignName?: string, campaignId?: number) => {
      const form = new FormData()
      form.append('file', file)
      if (campaignId) {
        form.append('campaign_id', String(campaignId))
      }
      if (campaignName) {
        form.append('campaign_name', campaignName)
      }
      return request<ImportReport>('/api/v1/leads/upload', {
        method: 'POST',
        body: form,
      })
    },
    pause: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/pause`, { method: 'POST' }),
    resume: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/resume`, { method: 'POST' }),
    markReplied: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/mark-replied`, { method: 'POST' }),
    markWon: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/mark-won`, { method: 'POST' }),
    markNotInterested: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/mark-not-interested`, { method: 'POST' }),
    skipStep: (id: number) =>
      request<Lead>(`/api/v1/leads/${id}/skip-step`, { method: 'POST' }),
    bulkValidate: (params?: { campaign_id?: number; campaign_name?: string; allow_risky?: boolean }) =>
      request<{ total_checked: number; valid_count: number; invalid_count: number; risky_count: number }>(
        `/api/v1/leads/bulk-validate${buildQuery(params)}`,
        { method: 'POST' }
      ),
    personalize: (params?: { campaign_id?: number; campaign_name?: string }) =>
      request<{
        total: number
        personalized: number
        preserved: number
        needs_manual_note: number
        message: string
      }>(`/api/v1/leads/personalize${buildQuery(params)}`, { method: 'POST' }),

  },

  // Approval Queue Endpoints
  approval: {
    listPending: (params?: { page?: number; page_size?: number; campaign_id?: number }) =>
      request<PaginatedResult<ApprovalQueueItem>>(`/api/v1/approval/pending${buildQuery(params)}`),
    approve: (leadId: number) =>
      request<Lead>(`/api/v1/approval/${leadId}/approve`, { method: 'POST' }),
    approveMany: (leadIds: number[]) =>
      request<{ requested: number; approved: number; lead_ids: number[] }>('/api/v1/approval/approve-many', {
        method: 'POST',
        body: JSON.stringify({ lead_ids: leadIds }),
      }),
    editAndApprove: (leadId: number, newNote: string) =>
      request<Lead>(`/api/v1/approval/${leadId}/edit-and-approve`, {
        method: 'POST',
        body: JSON.stringify({ new_note: newNote }),
      }),
    reject: (leadId: number, reason?: string) =>
      request<Lead>(`/api/v1/approval/${leadId}/reject`, {
        method: 'POST',
        body: JSON.stringify({ reason }),
      }),
    regenerate: (leadId: number) =>
      request<Lead>(`/api/v1/approval/${leadId}/regenerate`, { method: 'POST' }),
  },

  // Enrollment Endpoints
  enrollment: {
    enroll: (campaignId?: number) =>
      request<EnrollmentResponse>('/api/v1/enrollment/enroll', {
        method: 'POST',
        body: JSON.stringify({ campaign_id: campaignId }),
      }),
  },

  // Planning & Capacity
  planning: {
    preview: (targetDate?: string) =>
      request<PlanningPreview>(`/api/v1/planning/preview${buildQuery({ target_date: targetDate })}`),
    capacity: (checkDate?: string) =>
      request<CapacityResponse>(`/api/v1/planning/capacity${buildQuery({ check_date: checkDate })}`),
    plan: (targetDate?: string) =>
      request<PlanningPreview>(`/api/v1/planning/plan${buildQuery({ target_date: targetDate })}`, {
        method: 'POST',
      }),
  },

  // Outbound Emails & Outbox
  emails: {
    list: (params?: { page?: number; page_size?: number; status?: string; lead_id?: number }) =>
      request<PaginatedResult<EmailMessage>>(`/api/v1/emails${buildQuery(params)}`),
    get: (id: number) =>
      request<EmailMessage>(`/api/v1/emails/${id}`),
    outbox: (params?: { page?: number; page_size?: number }) =>
      request<PaginatedResult<OutboxEmail>>(`/api/v1/emails/outbox/dry-run${buildQuery(params)}`),
  },

  // Inbound Replies
  replies: {
    list: (params?: {
      page?: number
      page_size?: number
      classification?: string
      lead_id?: number
      unhandled_only?: boolean
      to_email?: string
    }) =>
      request<PaginatedResult<Reply>>(`/api/v1/replies${buildQuery(params)}`),
    inboxes: () =>
      request<ReplyInbox[]>('/api/v1/replies/inboxes'),
    markHandled: (id: number) =>
      request<{ id: number; handled: boolean }>(`/api/v1/replies/${id}/mark-handled`, { method: 'POST' }),
    markWon: (id: number) =>
      request<{ id: number; status: string }>(`/api/v1/replies/${id}/mark-won`, { method: 'POST' }),
  },

  // Mailboxes (Multi-Account Sending & Rotation)
  mailboxes: {
    list: () =>
      request<Mailbox[]>('/api/v1/mailboxes'),
    create: (data: MailboxCreate) =>
      request<Mailbox>('/api/v1/mailboxes', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<MailboxCreate>) =>
      request<Mailbox>(`/api/v1/mailboxes/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<{ success: boolean; deleted_id: number }>(`/api/v1/mailboxes/${id}`, {
        method: 'DELETE',
      }),
    test: (id: number, toEmail?: string) =>
      request<{ success: boolean; detail: string; recipient: string; message_id?: string }>(
        `/api/v1/mailboxes/${id}/test${buildQuery({ to_email: toEmail })}`,
        { method: 'POST' }
      ),
  },

  // Sequence Templates
  templates: {
    getSequence: (campaignId: number) =>
      request<{ campaign_id: number; steps: unknown[]; yaml_content: string }>(
        `/api/v1/templates/sequence${buildQuery({ campaign_id: campaignId })}`
      ),
    updateSequence: (campaignId: number, yamlContent: string) =>
      request<{ campaign_id: number; updated_steps_count: number; status: string }>('/api/v1/templates/sequence', {
        method: 'PUT',
        body: JSON.stringify({ campaign_id: campaignId, yaml_content: yamlContent }),
      }),
    preview: (data: TemplatePreviewRequest) =>
      request<TemplatePreviewResponse>('/api/v1/templates/preview', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  // Suppression List
  suppression: {
    list: (params?: { page?: number; page_size?: number; search?: string; is_domain?: boolean }) =>
      request<PaginatedResult<SuppressionEntry>>(`/api/v1/suppression${buildQuery(params)}`),
    add: (entry: string, reason?: string, isDomain?: boolean) =>
      request<SuppressionEntry>('/api/v1/suppression', {
        method: 'POST',
        body: JSON.stringify({ entry, reason, is_domain: isDomain }),
      }),
    remove: (entry: string) =>
      request<{ removed: string; status: string }>(`/api/v1/suppression/${encodeURIComponent(entry)}`, {
        method: 'DELETE',
      }),
    import: (file: File, defaultReason?: string) => {
      const form = new FormData()
      form.append('file', file)
      return request<{ imported: number; status: string }>(
        `/api/v1/suppression/import${buildQuery({ default_reason: defaultReason })}`,
        {
          method: 'POST',
          body: form,
        }
      )
    },
    exportUrl: () => `${API_BASE_URL}/api/v1/suppression/export`,
  },

  // Metrics & Stats
  stats: {
    timeseries: (days: number = 30) =>
      request<TimeseriesPoint[]>(`/api/v1/stats/timeseries${buildQuery({ days })}`),
    funnel: () =>
      request<FunnelStats>('/api/v1/stats/funnel'),
    templates: () =>
      request<TemplateVariantStat[]>('/api/v1/stats/templates'),
    bounceRate: () =>
      request<BounceRateStats>('/api/v1/stats/bounce-rate'),
    capacity: () =>
      request<CapacityResponse>('/api/v1/stats/capacity'),
    health: () =>
      request<{
        health_score: number
        health_grade: string
        status: string
        metrics: {
          bounce_rate_100: number
          bounce_rate_7d: number
          failure_rate: number
          complaint_signals_7d: number
          unsubscribe_rate_100: number
        }
        reasons: string[]
      }>('/api/v1/stats/health'),
  },

  // Safety & Killswitch
  safety: {
    status: () =>
      request<SafetyStatus>('/api/v1/safety/status'),
    resume: (data: SafetyResumeRequest) =>
      request<{ status: string; message: string; note: string }>('/api/v1/safety/resume', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    trip: (reason?: string) =>
      request<{ status: string; message: string; reason: string }>('/api/v1/safety/killswitch', {
        method: 'POST',
        body: JSON.stringify({ reason: reason || 'Manually engaged from Safety dashboard' }),
      }),
  },

  // Settings
  settings: {
    get: () =>
      request<WhitelistedSettings>('/api/v1/settings'),
    update: (settings: Record<string, unknown>, confirm: boolean = false) =>
      request<{ status: string; settings: Record<string, unknown> }>('/api/v1/settings', {
        method: 'PUT',
        body: JSON.stringify({ settings, confirm }),
      }),
    sendTestEmail: (toEmail: string) =>
      request<{ success: boolean; detail: string; message_id?: string; recipient?: string }>('/api/v1/settings/test-email', {
        method: 'POST',
        body: JSON.stringify({ to_email: toEmail }),
      }),
    toggleLiveMode: (liveMode: boolean) =>
      request<{ live_mode: boolean; message: string }>('/api/v1/settings/toggle-live-mode', {
        method: 'POST',
        body: JSON.stringify({ live_mode: liveMode }),
      }),
  },

  // Live Updates SSE Stream
  events: {
    getStreamUrl: (lastEventId: number = 0) =>
      `${API_BASE_URL}/api/v1/events?last_event_id=${lastEventId}`,
  },
}
