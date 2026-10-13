import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Send,
  Play,
  Pause,
  Clock,
  Sparkles,
  Calendar,
  Layers,
  ShieldAlert,
  Loader2,
  RefreshCw,
  MailCheck,
  Users,
  ChevronDown,
  ChevronUp,
  Upload,
} from 'lucide-react'
import { api } from '@/api/client'
import type { Campaign, PlannedEmailItem, CampaignScheduleItem } from '@/api/types'
import { StatusChip } from '@/design/StatusChip'
import { useToast } from '@/design/Toast'
import { EmptyState, Skeleton } from '@/design/Feedback'

export function CampaignsPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [expandedScheduleCampId, setExpandedScheduleCampId] = useState<number | null>(null)

  // 1. Fetch Campaigns List
  const {
    data: campaignsData,
    isLoading: isCampaignsLoading,
    refetch: refetchCampaigns,
  } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api.campaigns.list({ page_size: 20 }),
  })

  // 2. Fetch Planning Preview & Capacity
  const {
    data: planPreview,
    isLoading: isPlanLoading,
    refetch: refetchPlan,
    isFetching: isPlanFetching,
  } = useQuery({
    queryKey: ['planPreview'],
    queryFn: () => api.planning.preview(),
  })

  const { data: capacityData } = useQuery({
    queryKey: ['dailyCapacity'],
    queryFn: () => api.planning.capacity(),
  })

  // Mutations
  const activateMutation = useMutation({
    mutationFn: (id: number) => api.campaigns.activate(id),
    onSuccess: () => {
      toast({ title: 'Campaign Activated', message: 'Outbound worker will now dispatch scheduled emails', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
    onError: (err: any) => {
      toast({ title: 'Activation Failed', message: err.message, type: 'error' })
    },
  })

  const pauseMutation = useMutation({
    mutationFn: (id: number) => api.campaigns.pause(id),
    onSuccess: () => {
      toast({ title: 'Campaign Paused', message: 'All dispatching held safely', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
    onError: (err: any) => {
      toast({ title: 'Pause Failed', message: err.message, type: 'error' })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: (id: number) => api.campaigns.resume(id),
    onSuccess: () => {
      toast({ title: 'Campaign Resumed', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
    onError: (err: any) => {
      toast({ title: 'Resume Failed', message: err.message, type: 'error' })
    },
  })

  const enrollMutation = useMutation({
    mutationFn: (campaignId?: number) => api.enrollment.enroll(campaignId),
    onSuccess: (data) => {
      toast({
        title: 'Leads Enrolled',
        message: `Successfully enrolled ${data.enrolled_count} approved leads into sequence!`,
        type: 'success',
      })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['planPreview'] })
    },
    onError: (err: any) => {
      toast({ title: 'Enrollment Failed', message: err.message, type: 'error' })
    },
  })

  const planDispatchesMutation = useMutation({
    mutationFn: () => api.planning.plan(),
    onSuccess: (data) => {
      toast({
        title: 'Dispatches Scheduled',
        message: `Scheduled ${data.planned_emails?.length || 0} emails into safety windows!`,
        type: 'success',
      })
      queryClient.invalidateQueries({ queryKey: ['planPreview'] })
      queryClient.invalidateQueries({ queryKey: ['dailyCapacity'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Planning Failed', message: err.message, type: 'error' })
    },
  })

  const campaigns: Campaign[] = campaignsData?.items || []
  const plannedEmails: PlannedEmailItem[] = planPreview?.planned_emails || []

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Campaigns & Outbound Schedule</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Manage cold outreach campaigns, activate sending, and inspect scheduled email windows.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              refetchCampaigns()
              refetchPlan()
            }}
            disabled={isPlanFetching}
            className="p-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Refresh Schedules"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isPlanFetching ? 'animate-spin' : ''}`} />
          </button>

          <button
            type="button"
            disabled={planDispatchesMutation.isPending}
            onClick={() => planDispatchesMutation.mutate()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-md shadow-xs transition-colors disabled:opacity-60 cursor-pointer"
          >
            {planDispatchesMutation.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Calendar className="w-3.5 h-3.5" />
            )}
            Run Send Planner
          </button>
        </div>
      </div>

      {/* Capacity & Safety Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
        <div className="p-3.5 bg-card border border-border rounded-lg space-y-1">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5 text-primary" /> Daily Sending Window
          </span>
          <div className="text-lg font-bold text-foreground">09:00 - 11:30 AM</div>
          <p className="text-[11px] text-muted-foreground">Recipient Local TZ (Tue, Wed, Thu only)</p>
        </div>

        <div className="p-3.5 bg-card border border-border rounded-lg space-y-1">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
            <MailCheck className="w-3.5 h-3.5 text-emerald-500" /> Daily Capacity
          </span>
          <div className="text-lg font-bold text-foreground">
            {capacityData ? `${capacityData.remaining_capacity} / ${capacityData.daily_capacity} left` : '20 / 20 left'}
          </div>
          <p className="text-[11px] text-muted-foreground">Ramp-up warm cap protected</p>
        </div>

        <div className="p-3.5 bg-card border border-border rounded-lg space-y-1">
          <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 text-amber-500" /> Dispatch Worker
          </span>
          <div className="text-lg font-bold text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            Active & Polling
          </div>
          <p className="text-[11px] text-muted-foreground">Polls scheduled queue every 30s</p>
        </div>
      </div>

      {/* Campaigns Section */}
      <div className="space-y-3">
        <h2 className="text-sm font-bold text-foreground flex items-center gap-2">
          <Layers className="w-4 h-4 text-primary" /> Active Outreach Campaigns
        </h2>

        {isCampaignsLoading ? (
          <Skeleton className="h-28 w-full rounded-lg" />
        ) : campaigns.length === 0 ? (
          <EmptyState
            icon={<Send className="w-7 h-7 text-muted-foreground" />}
            title="No Campaigns Found"
            description="Create an outreach campaign or import leads to get started."
          />
        ) : (
          <div className="grid gap-3">
            {campaigns.map((camp) => {
              const isActive = camp.status === 'active'
              const isPaused = camp.status === 'paused'

              return (
                <div
                  key={camp.id}
                  className="bg-card border border-border rounded-lg p-4 space-y-3 transition-shadow hover:shadow-xs"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border/50 pb-3">
                    <div>
                      <div className="flex items-center gap-2.5">
                        <span className="text-base font-bold text-foreground">{camp.name}</span>
                        <StatusChip
                          status={camp.status}
                          variant={isActive ? 'success' : isPaused ? 'warning' : 'neutral'}
                        />
                      </div>
                      <div className="text-xs text-muted-foreground mt-0.5 flex items-center gap-3">
                        <span>Niche: <strong>{camp.niche || 'B2B'}</strong></span>
                        <span>•</span>
                        <span>Total Leads: <strong>{camp.leads_count ?? 0}</strong></span>
                        <span>•</span>
                        <span>In Sequence: <strong>{camp.active_leads_count ?? 0}</strong></span>
                      </div>
                    </div>

                    {/* Campaign Action Buttons */}
                    <div className="flex items-center gap-2 flex-wrap">
                      <button
                        type="button"
                        onClick={() =>
                          setExpandedScheduleCampId(
                            expandedScheduleCampId === camp.id ? null : camp.id
                          )
                        }
                        className={`px-2.5 py-1.5 text-xs font-semibold rounded-md flex items-center gap-1.5 transition-colors cursor-pointer border ${
                          expandedScheduleCampId === camp.id
                            ? 'bg-indigo-600 text-white border-indigo-700 shadow-xs'
                            : 'bg-card hover:bg-muted text-foreground border-border'
                        }`}
                        title="View scheduled dispatches and steps table for this campaign"
                      >
                        <Calendar className="w-3.5 h-3.5" />
                        {expandedScheduleCampId === camp.id
                          ? 'Hide Schedule'
                          : `Schedule & Steps (${camp.active_leads_count ?? 0})`}
                        {expandedScheduleCampId === camp.id ? (
                          <ChevronUp className="w-3 h-3" />
                        ) : (
                          <ChevronDown className="w-3 h-3" />
                        )}
                      </button>

                      <Link
                        to={`/leads?campaign=${camp.id}`}
                        className="px-2.5 py-1.5 text-xs font-semibold bg-muted hover:bg-muted/80 text-foreground border border-border rounded-md flex items-center gap-1.5 transition-colors cursor-pointer"
                        title="View and filter all leads in this campaign"
                      >
                        <Users className="w-3.5 h-3.5 text-muted-foreground" />
                        View Leads ({camp.leads_count ?? 0})
                      </Link>

                      <Link
                        to={`/leads?campaign=${camp.id}&openUpload=1`}
                        className="px-2.5 py-1.5 text-xs font-semibold bg-primary/10 hover:bg-primary/20 text-primary border border-primary/20 rounded-md flex items-center gap-1.5 transition-colors cursor-pointer"
                        title="Add or import more leads directly into this campaign"
                      >
                        <Upload className="w-3.5 h-3.5" />
                        Add Leads
                      </Link>

                      <button
                        type="button"
                        disabled={enrollMutation.isPending}
                        onClick={() => enrollMutation.mutate(camp.id)}
                        className="px-2.5 py-1.5 text-xs font-semibold bg-indigo-50 dark:bg-indigo-950/40 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-md hover:bg-indigo-100 flex items-center gap-1.5 transition-colors cursor-pointer"
                        title="Enroll approved leads into 4-step sequence"
                      >
                        <Sparkles className="w-3.5 h-3.5" />
                        Enroll Leads
                      </button>

                      {isActive ? (
                        <button
                          type="button"
                          disabled={pauseMutation.isPending}
                          onClick={() => pauseMutation.mutate(camp.id)}
                          className="px-3 py-1.5 text-xs font-semibold border border-amber-300 dark:border-amber-800 text-amber-700 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40 rounded-md flex items-center gap-1.5 transition-colors cursor-pointer"
                        >
                          <Pause className="w-3.5 h-3.5" /> Pause
                        </button>
                      ) : isPaused ? (
                        <button
                          type="button"
                          disabled={resumeMutation.isPending}
                          onClick={() => resumeMutation.mutate(camp.id)}
                          className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
                        >
                          <Play className="w-3.5 h-3.5" /> Resume Campaign
                        </button>
                      ) : (
                        <button
                          type="button"
                          disabled={activateMutation.isPending}
                          onClick={() => activateMutation.mutate(camp.id)}
                          className="px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
                        >
                          <Play className="w-3.5 h-3.5" /> Activate Campaign
                        </button>
                      )}
                    </div>
                  </div>

                  {/* 4 Sequence Steps Visualizer */}
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1 text-xs">
                    <div className="p-2.5 bg-muted/30 rounded-md border border-border/60">
                      <div className="font-semibold text-foreground">Step 1: Hook</div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">Day 0 (Initial observation)</div>
                    </div>
                    <div className="p-2.5 bg-muted/30 rounded-md border border-border/60">
                      <div className="font-semibold text-foreground">Step 2: Value</div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">+3 days (Case study)</div>
                    </div>
                    <div className="p-2.5 bg-muted/30 rounded-md border border-border/60">
                      <div className="font-semibold text-foreground">Step 3: Follow-up</div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">+4 days (Short nudge)</div>
                    </div>
                    <div className="p-2.5 bg-muted/30 rounded-md border border-border/60">
                      <div className="font-semibold text-foreground">Step 4: Breakup</div>
                      <div className="text-[11px] text-muted-foreground mt-0.5">+7 days (Graceful exit)</div>
                    </div>
                  </div>

                  {/* Inline Campaign Schedule Viewer */}
                  {expandedScheduleCampId === camp.id && (
                    <CampaignScheduleViewer campaignId={camp.id} campaignName={camp.name} />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {/* Scheduled Outbound Queue */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-bold text-foreground flex items-center gap-2">
            <Clock className="w-4 h-4 text-indigo-500" /> Scheduled Outbound Dispatches ({plannedEmails.length})
          </h2>
          {plannedEmails.length > 0 && (
            <span className="text-xs text-muted-foreground">
              Delivery controlled by recipient timezone & safety windows
            </span>
          )}
        </div>

        {isPlanLoading ? (
          <Skeleton className="h-32 w-full rounded-lg" />
        ) : plannedEmails.length === 0 ? (
          <EmptyState
            icon={<Clock className="w-7 h-7 text-muted-foreground" />}
            title="No Emails Scheduled in Next Window"
            description="Click 'Run Send Planner' above to calculate dispatch slots for your enrolled leads."
          />
        ) : (
          <div className="border border-border rounded-lg overflow-hidden bg-card text-xs">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="border-b border-border bg-muted/40 text-muted-foreground">
                    <th className="p-2.5 font-semibold">Recipient</th>
                    <th className="p-2.5 font-semibold">Company</th>
                    <th className="p-2.5 font-semibold">Step</th>
                    <th className="p-2.5 font-semibold">Subject</th>
                    <th className="p-2.5 font-semibold">Scheduled Slot (UTC)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {plannedEmails.map((msg) => (
                    <tr key={msg.email_id} className="hover:bg-muted/30 transition-colors">
                      <td className="p-2.5 font-semibold text-foreground">{msg.email}</td>
                      <td className="p-2.5 text-muted-foreground">{msg.company || '—'}</td>
                      <td className="p-2.5">
                        <span className="px-1.5 py-0.5 rounded-sm bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-medium">
                          Step {msg.step}
                        </span>
                      </td>
                      <td className="p-2.5 text-foreground max-w-xs truncate" title={msg.subject}>
                        {msg.subject}
                      </td>
                      <td className="p-2.5 font-mono text-[11px] text-muted-foreground tabular-nums">
                        {msg.scheduled_at ? new Date(msg.scheduled_at).toLocaleString() : 'Pending'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function CampaignScheduleViewer({
  campaignId,
  campaignName,
}: {
  campaignId: number
  campaignName: string
}) {
  const [stepFilter, setStepFilter] = useState<number | 'all'>('all')

  const { data: scheduleItems, isLoading } = useQuery({
    queryKey: ['campaignSchedule', campaignId],
    queryFn: () => api.campaigns.schedule(campaignId),
  })

  const items: CampaignScheduleItem[] = scheduleItems || []
  const filtered = stepFilter === 'all' ? items : items.filter((i) => i.step === stepFilter)

  const step1Count = items.filter((i) => i.step === 1).length
  const step2Count = items.filter((i) => i.step === 2).length
  const step3Count = items.filter((i) => i.step === 3).length
  const step4Count = items.filter((i) => i.step === 4).length

  if (isLoading) {
    return <Skeleton className="h-32 w-full rounded-md mt-2" />
  }

  return (
    <div className="mt-3 p-3 bg-muted/20 border border-indigo-500/30 rounded-md space-y-3 animate-in fade-in-50 duration-200">
      {/* Header & Step filter tabs */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border/60 pb-2">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-semibold text-foreground flex items-center gap-1 mr-2">
            <Clock className="w-3.5 h-3.5 text-indigo-500" />
            "{campaignName}" Dispatches ({items.length}):
          </span>
          <button
            type="button"
            onClick={() => setStepFilter('all')}
            className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
              stepFilter === 'all'
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:text-foreground border border-border'
            }`}
          >
            All ({items.length})
          </button>
          <button
            type="button"
            onClick={() => setStepFilter(1)}
            className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
              stepFilter === 1
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:text-foreground border border-border'
            }`}
          >
            Step 1 Hook ({step1Count})
          </button>
          <button
            type="button"
            onClick={() => setStepFilter(2)}
            className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
              stepFilter === 2
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:text-foreground border border-border'
            }`}
          >
            Step 2 (+3d) ({step2Count})
          </button>
          <button
            type="button"
            onClick={() => setStepFilter(3)}
            className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
              stepFilter === 3
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:text-foreground border border-border'
            }`}
          >
            Step 3 (+4d) ({step3Count})
          </button>
          <button
            type="button"
            onClick={() => setStepFilter(4)}
            className={`px-2 py-0.5 rounded text-[11px] font-medium transition-colors cursor-pointer ${
              stepFilter === 4
                ? 'bg-primary text-primary-foreground'
                : 'bg-card text-muted-foreground hover:text-foreground border border-border'
            }`}
          >
            Step 4 Breakup ({step4Count})
          </button>
        </div>

        <Link
          to={`/leads?campaign=${campaignId}`}
          className="text-xs font-semibold text-indigo-600 dark:text-indigo-400 hover:underline flex items-center gap-1 shrink-0"
        >
          Open in Leads Table →
        </Link>
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div className="text-center py-4 text-xs text-muted-foreground italic">
          No dispatches or leads enrolled in this step yet. Click "Enroll Leads" above.
        </div>
      ) : (
        <div className="border border-border/80 rounded-md overflow-hidden bg-card text-xs max-h-72 overflow-y-auto">
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 bg-muted/90 backdrop-blur-xs z-10 border-b border-border text-[11px]">
              <tr className="text-muted-foreground">
                <th className="p-2 font-semibold">Recipient</th>
                <th className="p-2 font-semibold">Company</th>
                <th className="p-2 font-semibold">Step</th>
                <th className="p-2 font-semibold">Subject</th>
                <th className="p-2 font-semibold">Status</th>
                <th className="p-2 font-semibold">Scheduled / Sent Slot</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {filtered.map((item) => (
                <tr key={item.id} className="hover:bg-muted/30 transition-colors">
                  <td className="p-2 font-semibold text-foreground">{item.email}</td>
                  <td className="p-2 text-muted-foreground">{item.company || '—'}</td>
                  <td className="p-2">
                    <span className="px-1.5 py-0.5 rounded-sm bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 font-mono text-[10px]">
                      Step {item.step}
                    </span>
                  </td>
                  <td className="p-2 text-foreground max-w-xs truncate" title={item.subject}>
                    {item.subject}
                  </td>
                  <td className="p-2">
                    <span
                      className={`text-[10px] px-1.5 py-0.5 rounded-sm font-medium uppercase ${
                        item.status === 'scheduled'
                          ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                          : item.status === 'sent'
                          ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          : item.status === 'needs_review'
                          ? 'bg-rose-500/10 text-rose-600 dark:text-rose-400'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      {item.status}
                    </span>
                  </td>
                  <td className="p-2 text-[11px] text-muted-foreground font-mono tabular-nums">
                    {item.scheduled_at
                      ? new Date(item.scheduled_at).toLocaleString()
                      : item.sent_at
                      ? new Date(item.sent_at).toLocaleString()
                      : 'Draft / Pending'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

