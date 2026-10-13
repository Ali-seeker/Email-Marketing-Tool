import type { components } from './schema'

export type Schemas = components['schemas']

export type Campaign = Schemas['CampaignResponse']
export type CampaignCreate = Schemas['CampaignCreate']
export type CampaignUpdate = Schemas['CampaignUpdate']

export type Lead = Schemas['LeadResponse']
export type LeadTimeline = Schemas['LeadDetailResponse']
export type LeadTimelineEvent = Schemas['LeadTimelineEvent']
export type LeadTimelineEmail = Schemas['LeadEmailSummary'] & { body?: string | null }
export type LeadTimelineReply = Schemas['LeadReplySummary']

export interface RejectItem {
  row_number: number
  email: string | null
  reason: string
  raw_data?: Record<string, string>
}

export interface ImportReport {
  campaign_id: number
  campaign_name: string
  total_rows: number
  imported: number
  duplicates: number
  suppressed: number
  rejected: number
  reject_items: RejectItem[]
  rejects_file_path?: string | null
}

export type ApprovalQueueItem = Schemas['PendingApprovalItem']
export type ApproveManyRequest = Schemas['ApproveManyRequest']
export type EditAndApproveRequest = Schemas['EditAndApproveRequest']
export type RejectRequest = Schemas['RejectRequest']

export type EnrollmentResponse = Schemas['EnrollResponse']
export type PlanningPreview = Schemas['PlanningPreviewResponse']
export type PlannedEmailItem = Schemas['PlannedEmailItem']
export type CapacityResponse = Schemas['CapacityResponse']

export type EmailMessage = Schemas['EmailMessageResponse']
export type OutboxEmail = Schemas['OutboxEmailResponse']
export type Reply = Schemas['ReplyResponse']

export interface CampaignScheduleItem {
  id: number
  lead_id: number
  email: string
  company: string | null
  step: number
  subject: string
  status: string
  scheduled_at: string | null
  sent_at: string | null
  error: string | null
}

export type TemplatePreviewRequest = Schemas['TemplatePreviewRequest']
export type TemplatePreviewResponse = Schemas['TemplatePreviewResponse']
export type SequenceYamlRequest = Schemas['SequenceYamlRequest']

export type SuppressionEntry = Schemas['SuppressionResponse']
export type SuppressionCreate = Schemas['SuppressionCreate']

export type FunnelStats = Schemas['FunnelStatsResponse']
export type TimeseriesPoint = Schemas['DailyStatsItem']
export type TemplateVariantStat = Schemas['VariantStatsItem']

export interface BounceRateStats {
  bounce_rate_100_sends: number
  bounce_rate_7_days: number
  total_sent_100: number
  total_sent_7d: number
  sample_size_ok: boolean
}

export type SafetyStatus = Schemas['SafetyStatusResponse']
export type SafetyAlert = Schemas['SafetyAlertItem']
export type SafetyResumeRequest = Schemas['SafetyResumeRequest']

export type WhitelistedSettings = Record<string, unknown>
export type SettingsUpdateRequest = Schemas['SettingsUpdateRequest']

export interface PaginatedResult<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}
