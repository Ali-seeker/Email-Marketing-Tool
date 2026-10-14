import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  ShieldAlert,
  ShieldCheck,
  AlertTriangle,
  Flame,
  Activity,
  CheckCircle2,
  RefreshCw,
  Lock,
  Unlock,
  Ban,
  UserX,
  MailWarning,
  Plus,
  Trash2,
} from 'lucide-react'
import { api } from '@/api/client'
import { useToast } from '@/design/Toast'
import { StatusChip } from '@/design/StatusChip'
import { Skeleton, EmptyState } from '@/design/Feedback'

export function SafetyPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  // State for modals and suppression tabs
  const [activeTab, setActiveTab] = useState<'monitor' | 'suppression'>('monitor')
  const [isResumeModalOpen, setIsResumeModalOpen] = useState(false)
  const [ackText, setAckText] = useState('')
  const [resumeNote, setResumeNote] = useState('')

  // Suppression state
  const [newSuppressionEntry, setNewSuppressionEntry] = useState('')
  const [newSuppressionReason, setNewSuppressionReason] = useState('Manual blacklist')
  const [isDomainFlag, setIsDomainFlag] = useState(false)

  // 1. Fetch Safety Status
  const {
    data: safetyStatus,
    isLoading: isSafetyLoading,
    refetch: refetchSafety,
    isRefetching,
  } = useQuery({
    queryKey: ['safetyStatus'],
    queryFn: () => api.safety.status(),
    refetchInterval: 15000,
  })

  // 2. Fetch Suppression List
  const { data: suppressionData, isLoading: isSuppressionLoading } = useQuery({
    queryKey: ['suppressionList'],
    queryFn: () => api.suppression.list({ page_size: 50 }),
    enabled: activeTab === 'suppression',
  })

  // Emergency Trip Mutation
  const tripMutation = useMutation({
    mutationFn: () => api.safety.trip('Manually engaged emergency stop by operator from Safety Console'),
    onSuccess: () => {
      toast({
        title: 'Emergency Kill-Switch Activated',
        message: 'All outbound dispatch loops halted immediately.',
        type: 'error',
      })
      queryClient.invalidateQueries({ queryKey: ['safetyStatus'] })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
    onError: (err: any) => {
      toast({ title: 'Trip Failed', message: err.message, type: 'error' })
    },
  })

  // Resume Killswitch Mutation
  const resumeMutation = useMutation({
    mutationFn: () =>
      api.safety.resume({
        acknowledgement: ackText.trim(),
        note: resumeNote.trim(),
      }),
    onSuccess: () => {
      toast({
        title: 'Kill-Switch Disarmed',
        message: 'Outbound sending resumed at halved daily capacity for the next 3 days.',
        type: 'success',
      })
      setIsResumeModalOpen(false)
      setAckText('')
      setResumeNote('')
      queryClient.invalidateQueries({ queryKey: ['safetyStatus'] })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
    },
    onError: (err: any) => {
      toast({ title: 'Resumption Denied', message: err.message, type: 'error' })
    },
  })

  // Add Suppression Mutation
  const addSuppressionMutation = useMutation({
    mutationFn: () =>
      api.suppression.add(newSuppressionEntry.trim(), newSuppressionReason.trim(), isDomainFlag),
    onSuccess: () => {
      toast({ title: 'Suppression Added', type: 'success' })
      setNewSuppressionEntry('')
      queryClient.invalidateQueries({ queryKey: ['suppressionList'] })
    },
    onError: (err: any) => {
      toast({ title: 'Failed to Add Suppression', message: err.message, type: 'error' })
    },
  })

  // Remove Suppression Mutation
  const removeSuppressionMutation = useMutation({
    mutationFn: (entry: string) => api.suppression.remove(entry),
    onSuccess: () => {
      toast({ title: 'Suppression Removed', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['suppressionList'] })
    },
    onError: (err: any) => {
      toast({ title: 'Failed to Remove', message: err.message, type: 'error' })
    },
  })

  const isKillswitchActive = Boolean(safetyStatus?.killswitch_active)
  const healthScore = safetyStatus?.health_score ?? 100
  const isHealthy = healthScore >= 80 && !isKillswitchActive

  return (
    <div className="space-y-5">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <ShieldCheck className="w-5 h-5 text-emerald-500" /> Deliverability & Safety Guard
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Automated reputation protection, multi-window bounce stop limits, and emergency kill-switch controls.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <div className="flex items-center bg-muted/60 p-0.5 rounded-lg border border-border text-xs">
            <button
              onClick={() => setActiveTab('monitor')}
              className={`px-3 py-1 font-semibold rounded-md transition-colors cursor-pointer ${
                activeTab === 'monitor'
                  ? 'bg-card text-foreground shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Health Monitor
            </button>
            <button
              onClick={() => setActiveTab('suppression')}
              className={`px-3 py-1 font-semibold rounded-md transition-colors cursor-pointer ${
                activeTab === 'suppression'
                  ? 'bg-card text-foreground shadow-xs'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              Suppression List
            </button>
          </div>

          <button
            onClick={() => refetchSafety()}
            disabled={isRefetching}
            className="p-1.5 text-muted-foreground hover:text-foreground border border-border rounded-md hover:bg-muted transition-colors cursor-pointer"
            title="Refresh safety status"
          >
            <RefreshCw className={`w-4 h-4 ${isRefetching ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {activeTab === 'monitor' && (
        <>
          {/* Main Status Hero Banner */}
          {isSafetyLoading ? (
            <Skeleton className="h-28 w-full rounded-xl" />
          ) : isKillswitchActive ? (
            <div className="p-5 bg-red-500/10 border-2 border-red-500/40 rounded-xl space-y-3 animate-in fade-in-50">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                <div className="flex items-center gap-3">
                  <div className="w-12 h-12 rounded-full bg-red-500/20 text-red-600 dark:text-red-400 flex items-center justify-center shrink-0">
                    <Flame className="w-6 h-6 animate-pulse" />
                  </div>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="text-base font-bold text-red-600 dark:text-red-400">
                        EMERGENCY KILL-SWITCH ACTIVE
                      </span>
                      <StatusChip status="critical" variant="danger" />
                    </div>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      All outbound emails and dispatch worker loops are completely halted to protect sender domain.
                    </p>
                  </div>
                </div>

                <button
                  onClick={() => setIsResumeModalOpen(true)}
                  className="px-4 py-2 text-xs font-bold bg-red-600 hover:bg-red-700 text-white rounded-lg shadow-sm flex items-center gap-1.5 transition-colors cursor-pointer shrink-0"
                >
                  <Unlock className="w-4 h-4" /> Disarm & Resume Sending
                </button>
              </div>

              {safetyStatus?.reasons && safetyStatus.reasons.length > 0 && (
                <div className="p-3 bg-red-500/15 border border-red-500/30 rounded-lg text-xs space-y-1">
                  <span className="font-semibold text-red-700 dark:text-red-300">Triggers detected:</span>
                  <ul className="list-disc list-inside space-y-0.5 text-red-800 dark:text-red-200">
                    {safetyStatus.reasons.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : (
            <div
              className={`p-5 rounded-xl border flex flex-col sm:flex-row sm:items-center justify-between gap-4 ${
                isHealthy
                  ? 'bg-emerald-500/10 border-emerald-500/30'
                  : 'bg-amber-500/10 border-amber-500/30'
              }`}
            >
              <div className="flex items-center gap-4">
                <div
                  className={`w-14 h-14 rounded-full flex flex-col items-center justify-center font-bold shrink-0 ${
                    isHealthy
                      ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                      : 'bg-amber-500/20 text-amber-700 dark:text-amber-300'
                  }`}
                >
                  <span className="text-lg leading-none">{healthScore}</span>
                  <span className="text-[10px] uppercase font-mono opacity-80">/100</span>
                </div>

                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-base font-bold text-foreground">
                      {isHealthy ? 'Outbound Engine Healthy & Protected' : 'Deliverability Warning Active'}
                    </span>
                    <StatusChip
                      status={isHealthy ? 'healthy' : 'warning'}
                      variant={isHealthy ? 'success' : 'warning'}
                    />
                  </div>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    {isHealthy
                      ? 'All deliverability safety gates are passing. Real-time rate thresholds and complaint watchers active.'
                      : 'Some metrics are nearing thresholds. Review bounce and failure rates below.'}
                  </p>
                </div>
              </div>

              <button
                onClick={() => {
                  if (confirm('Are you sure you want to trigger the EMERGENCY KILLSWITCH? All scheduled sending will be instantly halted.')) {
                    tripMutation.mutate()
                  }
                }}
                disabled={tripMutation.isPending}
                className="px-3.5 py-2 text-xs font-semibold border border-red-300 dark:border-red-900/60 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 rounded-lg flex items-center gap-1.5 transition-colors cursor-pointer shrink-0"
              >
                <AlertTriangle className="w-3.5 h-3.5" /> Emergency Stop (Trip Killswitch)
              </button>
            </div>
          )}

          {/* Key Deliverability Metric Cards Grid */}
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <div className="p-3.5 bg-card rounded-lg border border-border space-y-1">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                <span>Bounce Rate (100)</span>
                <span className="text-[10px] font-mono text-muted-foreground">Threshold &lt; 2.0%</span>
              </span>
              <div className="text-xl font-bold text-foreground tabular-nums">
                {safetyStatus ? `${safetyStatus.bounce_rate_100.toFixed(1)}%` : '—'}
              </div>
              <p className="text-[11px] text-muted-foreground">Across last 100 sent emails</p>
            </div>

            <div className="p-3.5 bg-card rounded-lg border border-border space-y-1">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                <span>7-Day Bounce Rate</span>
                <span className="text-[10px] font-mono text-muted-foreground">Rolling 7d</span>
              </span>
              <div className="text-xl font-bold text-foreground tabular-nums">
                {safetyStatus ? `${safetyStatus.bounce_rate_7d.toFixed(1)}%` : '—'}
              </div>
              <p className="text-[11px] text-muted-foreground">Across past 7 calendar days</p>
            </div>

            <div className="p-3.5 bg-card rounded-lg border border-border space-y-1">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                <span>Spam Complaints</span>
                <span className="text-[10px] font-mono text-red-500">Instant Halt</span>
              </span>
              <div className="text-xl font-bold text-foreground tabular-nums flex items-center gap-1.5">
                {safetyStatus?.complaint_signals_7d ?? 0}
                {(safetyStatus?.complaint_signals_7d ?? 0) === 0 ? (
                  <CheckCircle2 className="w-4 h-4 text-emerald-500" />
                ) : (
                  <Flame className="w-4 h-4 text-red-500" />
                )}
              </div>
              <p className="text-[11px] text-muted-foreground">Abuse / spam replies detected</p>
            </div>

            <div className="p-3.5 bg-card rounded-lg border border-border space-y-1">
              <span className="text-[11px] font-semibold text-muted-foreground uppercase tracking-wider flex items-center justify-between">
                <span>Mailbox Auth Errors</span>
                <span className="text-[10px] font-mono text-amber-500">Zero Tolerance</span>
              </span>
              <div className="text-xl font-bold text-foreground tabular-nums">
                {safetyStatus?.auth_or_block_errors ?? 0}
              </div>
              <p className="text-[11px] text-muted-foreground">Credential or suspension signals</p>
            </div>
          </div>

          {/* Active Protection Policies Overview */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <h2 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
              <Activity className="w-4 h-4 text-primary" /> Active Safety Rules & Automated Guardrails
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
              <div className="p-3 rounded-lg bg-muted/30 border border-border/60 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <ShieldAlert className="w-3.5 h-3.5 text-indigo-500" /> Multi-Window Bounce Stop
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  Automatic kill-switch engagement if <strong>≥ 3 bounces</strong> occur in any window of 10 sends, or if rolling bounce rate exceeds <strong>2.0%</strong>.
                </p>
              </div>

              <div className="p-3 rounded-lg bg-muted/30 border border-border/60 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <MailWarning className="w-3.5 h-3.5 text-amber-500" /> Spam Reply & Keyword Filter
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  Incoming replies are scanned in real-time for keywords like <em>"spam"</em>, <em>"abuse"</em>, or <em>"harassment"</em>. Immediate lead suppression and safety trip.
                </p>
              </div>

              <div className="p-3 rounded-lg bg-muted/30 border border-border/60 space-y-1">
                <div className="font-semibold text-foreground flex items-center gap-1.5">
                  <Lock className="w-3.5 h-3.5 text-emerald-500" /> Resumption Penalty
                </div>
                <p className="text-[11px] text-muted-foreground leading-relaxed">
                  Disarming the kill-switch requires explicit typed acknowledgement and automatically <strong>halves daily dispatch capacity</strong> for 3 calendar days.
                </p>
              </div>
            </div>
          </div>

          {/* Recent Safety Alerts Audit Log */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <h2 className="text-xs font-bold text-foreground uppercase tracking-wider">
              Safety Event Log & Audit History
            </h2>
            {isSafetyLoading ? (
              <Skeleton className="h-20 w-full" />
            ) : !safetyStatus?.recent_alerts || safetyStatus.recent_alerts.length === 0 ? (
              <div className="text-xs text-muted-foreground italic py-3 text-center">
                No security or emergency kill-switch events recorded. All operations nominal.
              </div>
            ) : (
              <div className="border border-border rounded-md overflow-hidden text-xs">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-[11px] text-muted-foreground">
                      <th className="p-2 font-semibold">Event Type</th>
                      <th className="p-2 font-semibold">Message / Reason</th>
                      <th className="p-2 font-semibold">Timestamp</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {safetyStatus.recent_alerts.map((al) => (
                      <tr key={al.id} className="hover:bg-muted/30">
                        <td className="p-2 font-semibold">
                          <StatusChip status={al.type} variant={al.type === 'killswitch' ? 'danger' : 'warning'} />
                        </td>
                        <td className="p-2 text-foreground font-mono text-[11px]">{al.message}</td>
                        <td className="p-2 text-muted-foreground font-mono text-[11px]">
                          {new Date(al.created_at).toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      {/* Suppression List Tab */}
      {activeTab === 'suppression' && (
        <div className="space-y-4">
          {/* Add Suppression Box */}
          <div className="p-4 bg-card border border-border rounded-lg space-y-3">
            <h2 className="text-xs font-bold text-foreground uppercase tracking-wider flex items-center gap-1.5">
              <UserX className="w-4 h-4 text-primary" /> Add Email or Domain to Global Suppression
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-2 text-xs">
              <div className="sm:col-span-5">
                <input
                  type="text"
                  placeholder="e.g. competitor@example.com or @domain.com"
                  value={newSuppressionEntry}
                  onChange={(e) => setNewSuppressionEntry(e.target.value)}
                  className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground"
                />
              </div>
              <div className="sm:col-span-4">
                <input
                  type="text"
                  placeholder="Reason (e.g. Unsubscribed, Competitor, Bounce)"
                  value={newSuppressionReason}
                  onChange={(e) => setNewSuppressionReason(e.target.value)}
                  className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground"
                />
              </div>
              <div className="sm:col-span-3 flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
                  <input
                    type="checkbox"
                    checked={isDomainFlag}
                    onChange={(e) => setIsDomainFlag(e.target.checked)}
                    className="rounded border-border"
                  />
                  Is Domain
                </label>
                <button
                  type="button"
                  disabled={!newSuppressionEntry.trim() || addSuppressionMutation.isPending}
                  onClick={() => addSuppressionMutation.mutate()}
                  className="px-3 h-8 font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md flex items-center gap-1 shadow-xs disabled:opacity-50 cursor-pointer ml-auto"
                >
                  <Plus className="w-3.5 h-3.5" /> Add
                </button>
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Any email or domain on the suppression list is strictly filtered out by the central gatekeeper and will NEVER receive outreach.
            </p>
          </div>

          {/* Suppression Records Table */}
          <div className="bg-card border border-border rounded-lg p-4 space-y-3">
            <h2 className="text-xs font-bold text-foreground uppercase tracking-wider">
              Suppressed Entries ({suppressionData?.total ?? 0})
            </h2>

            {isSuppressionLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : !suppressionData?.items || suppressionData.items.length === 0 ? (
              <EmptyState
                icon={<Ban className="w-7 h-7 text-muted-foreground" />}
                title="Suppression List Empty"
                description="No suppressed contacts or domains yet. Use the form above to add an entry."
              />
            ) : (
              <div className="border border-border rounded-md overflow-hidden text-xs">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-border bg-muted/40 text-[11px] text-muted-foreground">
                      <th className="p-2.5 font-semibold">Entry / Target</th>
                      <th className="p-2.5 font-semibold">Type</th>
                      <th className="p-2.5 font-semibold">Reason</th>
                      <th className="p-2.5 font-semibold">Added On</th>
                      <th className="p-2.5 font-semibold text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/60">
                    {suppressionData.items.map((row) => (
                      <tr key={row.email} className="hover:bg-muted/30">
                        <td className="p-2.5 font-semibold text-foreground">{row.email}</td>
                        <td className="p-2.5">
                          <span className="px-1.5 py-0.5 rounded text-[10px] bg-muted font-medium text-muted-foreground uppercase">
                            {row.is_domain ? 'Domain' : 'Email'}
                          </span>
                        </td>
                        <td className="p-2.5 text-muted-foreground">{row.reason || '—'}</td>
                        <td className="p-2.5 text-muted-foreground font-mono text-[11px]">
                          {new Date(row.created_at).toLocaleDateString()}
                        </td>
                        <td className="p-2.5 text-right">
                          <button
                            type="button"
                            onClick={() => removeSuppressionMutation.mutate(row.email)}
                            className="p-1 text-red-500 hover:text-red-700 hover:bg-red-50 dark:hover:bg-red-950/40 rounded transition-colors cursor-pointer"
                            title="Remove from suppression list"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Disarm & Resume Modal */}
      {isResumeModalOpen && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in-0"
        >
          <div className="bg-card w-full max-w-lg rounded-xl border border-border shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <h3 className="text-base font-bold text-foreground flex items-center gap-2">
                <Unlock className="w-4 h-4 text-emerald-500" /> Disarm & Resume Outbound Sending
              </h3>
              <button
                onClick={() => setIsResumeModalOpen(false)}
                className="text-muted-foreground hover:text-foreground text-sm font-semibold"
              >
                ✕
              </button>
            </div>

            <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-xs text-amber-800 dark:text-amber-200 space-y-1">
              <p className="font-semibold">⚠️ Mandatory Deliverability Safeguard:</p>
              <p>
                Disarming the kill-switch will automatically <strong>cut your daily send volume in half for 3 calendar days</strong> to rebuild sender reputation safely.
              </p>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block font-semibold text-foreground mb-1">
                  Type Acknowledgement Phrase:
                </label>
                <div className="flex items-center justify-between mb-1">
                  <p className="text-[11px] text-muted-foreground">
                    Type <strong>I ACKNOWLEDGE AND RESUME</strong> exactly:
                  </p>
                  <button
                    type="button"
                    onClick={() => setAckText('I ACKNOWLEDGE AND RESUME')}
                    className="text-primary hover:underline text-[11px] font-semibold cursor-pointer"
                  >
                    Click to Auto-Fill
                  </button>
                </div>
                <input
                  type="text"
                  placeholder="I ACKNOWLEDGE AND RESUME"
                  value={ackText}
                  onChange={(e) => setAckText(e.target.value)}
                  className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md font-mono text-xs text-foreground"
                  autoFocus
                />
              </div>

              <div>
                <label className="block font-semibold text-foreground mb-1">
                  Audit Explanation Note (Min 5 chars):
                </label>
                <textarea
                  placeholder="Explain why it is safe to resume (e.g. Fixed DNS SPF record, suppressed bad lead domains)..."
                  value={resumeNote}
                  onChange={(e) => setResumeNote(e.target.value)}
                  className="w-full h-20 p-2.5 bg-muted/40 border border-border rounded-md text-xs text-foreground resize-none"
                />
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <button
                type="button"
                onClick={() => setIsResumeModalOpen(false)}
                className="px-3 py-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={
                  ackText.trim() !== 'I ACKNOWLEDGE AND RESUME' ||
                  resumeNote.trim().length < 5 ||
                  resumeMutation.isPending
                }
                onClick={() => resumeMutation.mutate()}
                className="px-4 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md shadow-xs disabled:opacity-50 cursor-pointer"
              >
                {resumeMutation.isPending ? 'Disarming...' : 'Confirm & Resume Sending'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
