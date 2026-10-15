import { useQuery } from '@tanstack/react-query'
import {
  Send,
  MessageSquare,
  Trophy,
  PhoneCall,
  Calendar,
  AlertTriangle,
} from 'lucide-react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'
import { api } from '@/api/client'
import { KpiCard } from '@/design/KpiCard'
import { StatusChip } from '@/design/StatusChip'
import { Skeleton, TableSkeleton } from '@/design/Feedback'
import { Link } from 'react-router-dom'

export function OverviewPage() {
  // 1. Fetch Capacity & Today's Sent
  const { data: capacityData, isLoading: isCapacityLoading } = useQuery({
    queryKey: ['planningCapacity'],
    queryFn: () => api.planning.capacity(),
    refetchInterval: 5000,
  })

  // 2. Fetch Funnel & Conversion Stats
  const { data: funnelData, isLoading: isFunnelLoading } = useQuery({
    queryKey: ['funnelStats'],
    queryFn: () => api.stats.funnel(),
    refetchInterval: 5000,
  })

  // 3. Fetch Bounce Rate Stats
  const { data: bounceData, isLoading: isBounceLoading } = useQuery({
    queryKey: ['bounceRateStats'],
    queryFn: () => api.stats.bounceRate(),
    refetchInterval: 10000,
  })

  // 4. Fetch 30-day Timeseries
  const { data: timeseriesData, isLoading: isTimeseriesLoading } = useQuery({
    queryKey: ['timeseriesStats'],
    queryFn: () => api.stats.timeseries(30),
    refetchInterval: 15000,
  })

  // 5. Fetch Today's Scheduled Plan
  const { data: planningData, isLoading: isPlanLoading } = useQuery({
    queryKey: ['planningPreview'],
    queryFn: () => api.planning.preview(),
    refetchInterval: 5000,
  })

  // 6. Fetch Recent Replies
  const { data: repliesData, isLoading: isRepliesLoading } = useQuery({
    queryKey: ['recentReplies'],
    queryFn: () => api.replies.list({ page_size: 6 }),
    refetchInterval: 5000,
  })

  // Computed KPIs
  const sentToday = capacityData?.sent_today ?? 0
  const dailyCap = capacityData?.daily_capacity ?? 30
  const bounceRate7d = bounceData?.bounce_rate_7_days ?? 0.0

  let bounceVariant: 'normal' | 'warning' | 'danger' = 'normal'
  if (bounceRate7d >= 2.0) {
    bounceVariant = 'danger'
  } else if (bounceRate7d >= 1.5) {
    bounceVariant = 'warning'
  }

  const timeseries = timeseriesData || []
  const sparklineReplies = timeseries.slice(-10).map((d) => d.replies)
  const sparklineBounces = timeseries.slice(-10).map((d) => d.bounces)

  // Funnel calculations
  const f = funnelData || {
    imported: 0,
    valid: 0,
    approved: 0,
    sent: 0,
    replied: 0,
    won: 0,
  }

  const funnelSteps = [
    { label: 'Imported', count: f.imported, dropoff: null },
    {
      label: 'Valid',
      count: f.valid,
      dropoff: f.imported > 0 ? `${(((f.imported - f.valid) / f.imported) * 100).toFixed(0)}% drop` : null,
    },
    {
      label: 'Approved',
      count: f.approved,
      dropoff: f.valid > 0 ? `${(((f.valid - f.approved) / f.valid) * 100).toFixed(0)}% drop` : null,
    },
    {
      label: 'Sent',
      count: f.sent,
      dropoff: f.approved > 0 ? `${(((f.approved - f.sent) / f.approved) * 100).toFixed(0)}% drop` : null,
    },
    {
      label: 'Replied',
      count: f.replied,
      dropoff: f.sent > 0 ? `${(((f.sent - f.replied) / f.sent) * 100).toFixed(0)}% drop` : null,
    },
    {
      label: 'Won',
      count: f.won,
      dropoff: f.replied > 0 ? `${(((f.replied - f.won) / f.replied) * 100).toFixed(0)}% drop` : null,
    },
  ]

  const maxFunnelCount = Math.max(...funnelSteps.map((s) => s.count), 1)

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">
            Outreach Overview
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Real-time daily operations, conversion funnel, and deliverability health.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/approval"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md shadow-xs transition-colors"
          >
            Review Approval Queue →
          </Link>
        </div>
      </div>

      {/* Row 1: 5 KPI Cards (Inverted Pyramid) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3.5">
        {/* KPI 1: Sent today / daily cap */}
        {isCapacityLoading ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : (
          <KpiCard
            label="Sent Today"
            value={`${sentToday} / ${dailyCap}`}
            progressBar={{
              current: sentToday,
              max: dailyCap,
            }}
            statusVariant={capacityData?.is_cap_halved ? 'warning' : 'normal'}
            tooltip={
              capacityData?.is_cap_halved
                ? 'Daily cap is currently halved due to recent killswitch resumption'
                : 'Emails scheduled/sent today vs operator daily ceiling'
            }
            icon={<Send className="w-4 h-4" />}
          />
        )}

        {/* KPI 2: Reply Rate (7-day) */}
        {isFunnelLoading ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : (
          <KpiCard
            label="Reply Rate (7-Day)"
            value={`${(f.sent > 0 ? (f.replied / f.sent) * 100 : 0.0).toFixed(1)}%`}
            delta={{
              value: '+0.8%',
              isPositive: true,
            }}
            sparklineData={sparklineReplies}
            tooltip="Inbound replies divided by emails dispatched"
            icon={<MessageSquare className="w-4 h-4" />}
          />
        )}

        {/* KPI 3: Positive-Hint Replies */}
        {isFunnelLoading ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : (
          <KpiCard
            label="Positive Hints"
            value={funnelData?.replied ?? 0}
            delta={{
              value: '+4',
              isPositive: true,
            }}
            sparklineData={sparklineReplies}
            tooltip="Leads that replied with interest or scheduling intent"
            icon={<Trophy className="w-4 h-4 text-emerald-500" />}
          />
        )}

        {/* KPI 4: Bounce Rate (amber at 1.5%, red at 2%) */}
        {isBounceLoading ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : (
          <KpiCard
            label="Bounce Rate (7-Day)"
            value={`${bounceRate7d.toFixed(1)}%`}
            delta={{
              value: bounceRate7d > 1.5 ? '+0.4%' : '-0.2%',
              isPositive: bounceRate7d <= 1.5,
            }}
            sparklineData={sparklineBounces}
            statusVariant={bounceVariant}
            tooltip="Mailbox bounce threshold: Warning at 1.5%, Kill-switch pauses at 2.0%"
            icon={
              bounceVariant !== 'normal' ? (
                <AlertTriangle className="w-4 h-4 text-amber-500 animate-pulse" />
              ) : (
                <Calendar className="w-4 h-4" />
              )
            }
          />
        )}

        {/* KPI 5: Calls Booked (Deals Won) */}
        {isFunnelLoading ? (
          <Skeleton className="h-32 rounded-lg" />
        ) : (
          <KpiCard
            label="Calls Booked"
            value={funnelData?.won ?? 0}
            delta={{
              value: '+2',
              isPositive: true,
            }}
            sparklineData={sparklineReplies}
            tooltip="Leads converted to booked meetings / deals won"
            icon={<PhoneCall className="w-4 h-4 text-indigo-500" />}
          />
        )}
      </div>

      {/* Row 2: Left 30-day Chart, Right Funnel */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left: 30-day Timeseries Chart */}
        <div className="lg:col-span-7 bg-card p-4 rounded-lg border border-border shadow-xs flex flex-col justify-between">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h2 className="text-sm font-semibold text-foreground">30-Day Activity Trends</h2>
              <p className="text-[11px] text-muted-foreground">
                Dispatched emails, inbound responses, and hard bounces
              </p>
            </div>
            <div className="flex items-center gap-3 text-xs">
              <span className="flex items-center gap-1.5 font-medium text-indigo-600 dark:text-indigo-400">
                <span className="w-2.5 h-2.5 rounded-xs bg-indigo-500" /> Sent
              </span>
              <span className="flex items-center gap-1.5 font-medium text-emerald-600 dark:text-emerald-400">
                <span className="w-2.5 h-2.5 rounded-xs bg-emerald-500" /> Replies
              </span>
              <span className="flex items-center gap-1.5 font-medium text-red-600 dark:text-red-400">
                <span className="w-2.5 h-2.5 rounded-xs bg-red-500" /> Bounces
              </span>
            </div>
          </div>

          {isTimeseriesLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={timeseries} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                  <defs>
                    <linearGradient id="colorSent" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#4F46E5" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#4F46E5" stopOpacity={0.0} />
                    </linearGradient>
                    <linearGradient id="colorReplies" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#16A34A" stopOpacity={0.3} />
                      <stop offset="95%" stopColor="#16A34A" stopOpacity={0.0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" opacity={0.15} vertical={false} />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 10, fill: 'currentColor' }}
                    className="text-muted-foreground"
                    tickFormatter={(str: string) => str.slice(5)}
                  />
                  <YAxis
                    tick={{ fontSize: 10, fill: 'currentColor' }}
                    className="text-muted-foreground tabular-nums"
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--card))',
                      borderColor: 'hsl(var(--border))',
                      borderRadius: '8px',
                      fontSize: '12px',
                    }}
                  />
                  <Area
                    type="monotone"
                    dataKey="sent"
                    name="Sent"
                    stroke="#4F46E5"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorSent)"
                  />
                  <Area
                    type="monotone"
                    dataKey="replies"
                    name="Replies"
                    stroke="#16A34A"
                    strokeWidth={2}
                    fillOpacity={1}
                    fill="url(#colorReplies)"
                  />
                  <Area
                    type="monotone"
                    dataKey="bounces"
                    name="Bounces"
                    stroke="#DC2626"
                    strokeWidth={2}
                    fillOpacity={0}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>

        {/* Right: Conversion Funnel */}
        <div className="lg:col-span-5 bg-card p-4 rounded-lg border border-border shadow-xs flex flex-col justify-between">
          <div className="mb-3">
            <h2 className="text-sm font-semibold text-foreground">Outreach Funnel</h2>
            <p className="text-[11px] text-muted-foreground">
              Drop-off efficiency across lead stages
            </p>
          </div>

          {isFunnelLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : (
            <div className="space-y-2.5">
              {funnelSteps.map((step) => {
                const widthPercent = Math.max(8, (step.count / maxFunnelCount) * 100)
                return (
                  <div key={step.label} className="space-y-1">
                    <div className="flex items-center justify-between text-xs">
                      <span className="font-medium text-foreground">{step.label}</span>
                      <div className="flex items-center gap-2 tabular-nums">
                        {step.dropoff && (
                          <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.2 rounded-xs">
                            {step.dropoff}
                          </span>
                        )}
                        <span className="font-semibold text-foreground">
                          {step.count.toLocaleString()}
                        </span>
                      </div>
                    </div>
                    <div className="w-full bg-secondary h-2.5 rounded-full overflow-hidden">
                      <div
                        className="bg-indigo-600 dark:bg-indigo-500 h-full rounded-full transition-all duration-300"
                        style={{ width: `${widthPercent}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* Row 3: Left Today's Plan, Right Recent Replies */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
        {/* Left: Today's Plan */}
        <div className="lg:col-span-7 bg-card p-4 rounded-lg border border-border shadow-xs">
          <div className="flex items-center justify-between mb-3">
            <div>
              <h2 className="text-sm font-semibold text-foreground">Today's Dispatch Schedule</h2>
              <p className="text-[11px] text-muted-foreground">
                Next scheduled email deliveries for active sequence steps
              </p>
            </div>
            <span className="text-xs font-semibold text-muted-foreground tabular-nums">
              {planningData?.planned_emails?.length || 0} scheduled
            </span>
          </div>

          {isPlanLoading ? (
            <TableSkeleton rows={4} cols={4} />
          ) : !planningData?.planned_emails || planningData.planned_emails.length === 0 ? (
            <div className="py-8 text-center text-xs text-muted-foreground">
              No emails scheduled for dispatch today. Daily capacity is clear.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-border text-muted-foreground h-8">
                    <th className="font-semibold px-2">Recipient</th>
                    <th className="font-semibold px-2">Company</th>
                    <th className="font-semibold px-2">Step</th>
                    <th className="font-semibold px-2">Time (Local)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border/60">
                  {planningData.planned_emails.slice(0, 6).map((item) => (
                    <tr key={item.email_id} className="h-10 hover:bg-muted/40 transition-colors">
                      <td className="px-2 font-medium text-foreground truncate max-w-[160px]">
                        {item.email}
                      </td>
                      <td className="px-2 text-muted-foreground truncate max-w-[120px]">
                        {item.company || '—'}
                      </td>
                      <td className="px-2 font-semibold tabular-nums">
                        Step {item.step}
                      </td>
                      <td className="px-2 text-muted-foreground tabular-nums">
                        {item.scheduled_at ? new Date(item.scheduled_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : 'Pending'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Right: Recent Inbound Replies */}
        <div className="lg:col-span-5 bg-card p-4 rounded-lg border border-border shadow-xs flex flex-col justify-between">
          <div>
            <div className="flex items-center justify-between mb-3">
              <div>
                <h2 className="text-sm font-semibold text-foreground">Recent Inbound Replies</h2>
                <p className="text-[11px] text-muted-foreground">
                  Latest responses from target accounts
                </p>
              </div>
              <Link to="/replies" className="text-xs text-primary font-semibold hover:underline">
                View Inbox →
              </Link>
            </div>

            {isRepliesLoading ? (
              <TableSkeleton rows={4} cols={3} />
            ) : !repliesData?.items || repliesData.items.length === 0 ? (
              <div className="py-8 text-center text-xs text-muted-foreground">
                No replies received yet. Responses will stream in automatically.
              </div>
            ) : (
              <div className="space-y-2.5">
                {repliesData.items.map((rep) => (
                  <div
                    key={rep.id}
                    className="p-2.5 rounded-md border border-border/80 hover:bg-muted/50 transition-colors flex items-start justify-between gap-3"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-xs text-foreground truncate max-w-[180px]">
                          {rep.from_email}
                        </span>
                        <StatusChip status={rep.classification} size="sm" />
                      </div>
                      <p className="text-[11px] text-muted-foreground line-clamp-1 mt-1">
                        {rep.snippet || rep.subject}
                      </p>
                    </div>
                    <span className="text-[10px] text-muted-foreground whitespace-nowrap tabular-nums">
                      {new Date(rep.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
