import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Settings as SettingsIcon,
  Save,
  ShieldCheck,
  Cpu,
  Calendar,
  RotateCcw,
  Sliders,
  Mail,
  Send,
  CheckCircle2,
  AlertCircle,
  Loader2,
} from 'lucide-react'
import { api } from '@/api/client'
import { useToast } from '@/design/Toast'
import { Skeleton } from '@/design/Feedback'

const DAYS_OF_WEEK = [
  { key: 'mon', label: 'Mon' },
  { key: 'tue', label: 'Tue' },
  { key: 'wed', label: 'Wed' },
  { key: 'thu', label: 'Thu' },
  { key: 'fri', label: 'Fri' },
  { key: 'sat', label: 'Sat' },
  { key: 'sun', label: 'Sun' },
]

const COMMON_TIMEZONES = [
  'America/New_York',
  'America/Chicago',
  'America/Denver',
  'America/Los_Angeles',
  'Europe/London',
  'Europe/Berlin',
  'Asia/Karachi',
  'Asia/Dubai',
  'UTC',
]

export function SettingsPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  // 1. Fetch current runtime settings
  const { data: rawSettings, isLoading } = useQuery({
    queryKey: ['systemSettings'],
    queryFn: () => api.settings.get(),
  })

  // Form local state
  const [form, setForm] = useState<Record<string, any>>({})
  const [isDirty, setIsDirty] = useState(false)

  // Populate local form on query data arrival
  useEffect(() => {
    if (rawSettings) {
      setForm({ ...rawSettings })
      setIsDirty(false)
    }
  }, [rawSettings])

  // Update mutation
  const updateMutation = useMutation({
    mutationFn: (confirm: boolean = false) => {
      // Exclude read-only status fields like LIVE_MODE from update payload
      const { LIVE_MODE, ...updatable } = form
      return api.settings.update(updatable, confirm)
    },
    onSuccess: () => {
      toast({
        title: 'Settings Saved',
        message: 'Runtime configurations updated across background scheduler and worker.',
        type: 'success',
      })
      setIsDirty(false)
      queryClient.invalidateQueries({ queryKey: ['systemSettings'] })
      queryClient.invalidateQueries({ queryKey: ['dailyCapacity'] })
      queryClient.invalidateQueries({ queryKey: ['planningPreview'] })
    },
    onError: (err: any) => {
      // If server asks for confirmation
      if (err.message && err.message.includes('confirm=true')) {
        if (confirm(`${err.message}\n\nDo you want to confirm and force this safety override?`)) {
          updateMutation.mutate(true)
          return
        }
      }
      toast({ title: 'Update Failed', message: err.message, type: 'error' })
    },
  })

  // Test email state & mutation
  const [testEmail, setTestEmail] = useState('')
  const [testStatus, setTestStatus] = useState<{ success?: boolean; message?: string } | null>(null)

  const sendTestMutation = useMutation({
    mutationFn: (email: string) => api.settings.sendTestEmail(email),
    onSuccess: (data) => {
      setTestStatus({ success: true, message: data.detail })
      toast({
        title: 'Test Email Sent!',
        message: `Dispatched test email to ${data.recipient || testEmail}. Check your inbox!`,
        type: 'success',
      })
    },
    onError: (err: any) => {
      setTestStatus({ success: false, message: err.message })
      toast({
        title: 'Delivery Failed',
        message: err.message,
        type: 'error',
      })
    },
  })

  const handleFieldChange = (key: string, value: any) => {
    setForm((prev) => ({ ...prev, [key]: value }))
    setIsDirty(true)
  }

  const toggleDay = (dayKey: string) => {
    const currentDays: string[] = Array.isArray(form.SEND_DAYS) ? form.SEND_DAYS : ['tue', 'wed', 'thu']
    const nextDays = currentDays.includes(dayKey)
      ? currentDays.filter((d) => d !== dayKey)
      : [...currentDays, dayKey]
    handleFieldChange('SEND_DAYS', nextDays)
  }

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-48 w-full rounded-xl" />
        <Skeleton className="h-48 w-full rounded-xl" />
      </div>
    )
  }

  return (
    <div className="space-y-5 max-w-4xl">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground flex items-center gap-2">
            <SettingsIcon className="w-5 h-5 text-primary" /> Runtime System Settings
          </h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Configure safety thresholds, sending caps, working day windows, and AI generation parameters.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {isDirty && (
            <button
              type="button"
              onClick={() => {
                if (rawSettings) setForm({ ...rawSettings })
                setIsDirty(false)
              }}
              className="px-3 py-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground flex items-center gap-1.5 transition-colors cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" /> Reset
            </button>
          )}

          <button
            type="button"
            disabled={!isDirty || updateMutation.isPending}
            onClick={() => updateMutation.mutate(false)}
            className="px-4 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md shadow-xs disabled:opacity-50 flex items-center gap-1.5 transition-colors cursor-pointer"
          >
            <Save className="w-3.5 h-3.5" />
            {updateMutation.isPending ? 'Saving...' : 'Save Settings'}
          </button>
        </div>
      </div>

      {/* 1. Outbound Capacity & Rate Limits */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2 border-b border-border/60 pb-2.5">
          <Sliders className="w-4 h-4 text-indigo-500" />
          <h2 className="text-sm font-bold text-foreground">Outbound Sending Limits & Delays</h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="block font-semibold text-foreground mb-1">
              Max Emails Per Day (Per Mailbox)
            </label>
            <input
              type="number"
              min={1}
              max={30}
              value={form.MAX_EMAILS_PER_DAY_PER_MAILBOX ?? 20}
              onChange={(e) => handleFieldChange('MAX_EMAILS_PER_DAY_PER_MAILBOX', parseInt(e.target.value) || 20)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              Protected by HARD_MAX limit (30 max). Recommended: 15–20 for healthy domain warm-up.
            </p>
          </div>

          <div>
            <label className="block font-semibold text-foreground mb-1">
              Minimum Gap Between Sends (Seconds)
            </label>
            <input
              type="number"
              min={60}
              max={600}
              value={form.MIN_GAP_SECONDS ?? 120}
              onChange={(e) => handleFieldChange('MIN_GAP_SECONDS', parseInt(e.target.value) || 120)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              Default: 120s (2 minutes). Prevents email bursts to mimic realistic human sending.
            </p>
          </div>

          <div>
            <label className="block font-semibold text-foreground mb-1">
              Maximum Random Jitter Delay (Seconds)
            </label>
            <input
              type="number"
              min={0}
              max={300}
              value={form.MAX_JITTER_SECONDS ?? 180}
              onChange={(e) => handleFieldChange('MAX_JITTER_SECONDS', parseInt(e.target.value) || 180)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              Adds random variation (0 to 180s) to sending timestamps to defeat spam bot pattern detection.
            </p>
          </div>

          <div>
            <label className="block font-semibold text-foreground mb-1">
              Mailbox Fallback Timezone
            </label>
            <select
              value={form.MAILBOX_TZ || 'America/New_York'}
              onChange={(e) => handleFieldChange('MAILBOX_TZ', e.target.value)}
              className="w-full h-8 px-2.5 bg-card border border-border rounded-md text-foreground cursor-pointer font-mono"
            >
              {COMMON_TIMEZONES.map((tz) => (
                <option key={tz} value={tz}>
                  {tz}
                </option>
              ))}
            </select>
            <p className="text-[11px] text-muted-foreground mt-1">
              Used when lead timezone cannot be inferred from country.
            </p>
          </div>
        </div>
      </div>

      {/* 2. Schedule & Working Windows */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2 border-b border-border/60 pb-2.5">
          <Calendar className="w-4 h-4 text-emerald-500" />
          <h2 className="text-sm font-bold text-foreground">Allowed Outreach Schedule & Windows</h2>
        </div>

        <div className="space-y-3 text-xs">
          <div>
            <label className="block font-semibold text-foreground mb-1.5">
              Allowed Sending Weekdays (Recipient Local Time)
            </label>
            <div className="flex items-center gap-2">
              {DAYS_OF_WEEK.map((d) => {
                const isSelected = Array.isArray(form.SEND_DAYS) && form.SEND_DAYS.includes(d.key)
                return (
                  <button
                    key={d.key}
                    type="button"
                    onClick={() => toggleDay(d.key)}
                    className={`px-3 py-1.5 rounded-md font-semibold text-xs border transition-colors cursor-pointer ${
                      isSelected
                        ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                        : 'bg-muted/40 text-muted-foreground border-border hover:bg-muted hover:text-foreground'
                    }`}
                  >
                    {d.label}
                  </button>
                )
              })}
            </div>
            <p className="text-[11px] text-muted-foreground mt-1.5">
              Industry standard: Tuesday, Wednesday, Thursday for highest B2B open and conversion rates.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-1">
            <div>
              <label className="block font-semibold text-foreground mb-1">
                Outreach Start Date
              </label>
              <input
                type="text"
                value={form.OUTREACH_START_DATE || '2026-10-01'}
                onChange={(e) => handleFieldChange('OUTREACH_START_DATE', e.target.value)}
                placeholder="2026-10-01"
                className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
              />
              <p className="text-[11px] text-muted-foreground mt-1">
                No emails send before this date. Set to today or earlier for live dispatch.
              </p>
            </div>

            <div>
              <label className="block font-semibold text-foreground mb-1">
                Window Start Time (Local)
              </label>
              <input
                type="text"
                value={form.SEND_WINDOW_START || '09:00'}
                onChange={(e) => handleFieldChange('SEND_WINDOW_START', e.target.value)}
                placeholder="09:00"
                className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
              />
              <p className="text-[11px] text-muted-foreground mt-1">
                Recipient local window start.
              </p>
            </div>

            <div>
              <label className="block font-semibold text-foreground mb-1">
                Window End Time (Local)
              </label>
              <input
                type="text"
                value={form.SEND_WINDOW_END || '11:30'}
                onChange={(e) => handleFieldChange('SEND_WINDOW_END', e.target.value)}
                placeholder="11:30"
                className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
              />
              <p className="text-[11px] text-muted-foreground mt-1">
                Recipient local window end.
              </p>
            </div>
          </div>
        </div>
      </div>

      {/* 3. Deliverability Safety Policies */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2 border-b border-border/60 pb-2.5">
          <ShieldCheck className="w-4 h-4 text-emerald-500" />
          <h2 className="text-sm font-bold text-foreground">Safety Policies & Verification Gates</h2>
        </div>

        <div className="space-y-3 text-xs">
          <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30 border border-border/60">
            <div>
              <span className="font-semibold text-foreground block">
                Require Manual Personal Note Before Approval
              </span>
              <span className="text-[11px] text-muted-foreground">
                When enabled, leads without human-reviewed observations cannot be scheduled.
              </span>
            </div>
            <input
              type="checkbox"
              checked={Boolean(form.REQUIRE_PERSONAL_NOTE ?? true)}
              onChange={(e) => handleFieldChange('REQUIRE_PERSONAL_NOTE', e.target.checked)}
              className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
            />
          </div>

          <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30 border border-border/60">
            <div>
              <span className="font-semibold text-foreground block">
                Allow Risky (Catch-All) Email Validation
              </span>
              <span className="text-[11px] text-muted-foreground">
                Disabled by default to protect domain health. Catch-all domains carry higher bounce risk.
              </span>
            </div>
            <input
              type="checkbox"
              checked={Boolean(form.ALLOW_RISKY ?? false)}
              onChange={(e) => handleFieldChange('ALLOW_RISKY', e.target.checked)}
              className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
            />
          </div>

          <div className="flex items-center justify-between p-3 rounded-lg bg-muted/30 border border-border/60">
            <div>
              <span className="font-semibold text-foreground block">
                Auto-Approve AI Drafts (High Risk)
              </span>
              <span className="text-[11px] text-muted-foreground">
                Bypasses operator approval queue. Strictly discouraged unless using high-confidence leads.
              </span>
            </div>
            <input
              type="checkbox"
              checked={Boolean(form.AUTO_APPROVE ?? false)}
              onChange={(e) => handleFieldChange('AUTO_APPROVE', e.target.checked)}
              className="w-4 h-4 rounded border-border accent-primary cursor-pointer"
            />
          </div>

          <div className="pt-1">
            <label className="block font-semibold text-foreground mb-1">
              Bounce Rate Stop Threshold (%)
            </label>
            <input
              type="number"
              step="0.1"
              min={0.5}
              max={5.0}
              value={form.BOUNCE_RATE_STOP_THRESHOLD_PERCENT ?? 2.0}
              onChange={(e) => handleFieldChange('BOUNCE_RATE_STOP_THRESHOLD_PERCENT', parseFloat(e.target.value) || 2.0)}
              className="w-full sm:w-48 h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
            />
            <p className="text-[11px] text-muted-foreground mt-1">
              If bounce rate exceeds this value, emergency kill-switch automatically engages.
            </p>
          </div>
        </div>
      </div>

      {/* 4. AI & LLM Engine Settings */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-center gap-2 border-b border-border/60 pb-2.5">
          <Cpu className="w-4 h-4 text-purple-500" />
          <h2 className="text-sm font-bold text-foreground">AI Personalization Engine</h2>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
          <div>
            <label className="block font-semibold text-foreground mb-1">
              Active LLM Provider
            </label>
            <select
              value={form.LLM_PROVIDER || 'gemini'}
              onChange={(e) => handleFieldChange('LLM_PROVIDER', e.target.value)}
              className="w-full h-8 px-2.5 bg-card border border-border rounded-md text-foreground cursor-pointer font-mono"
            >
              <option value="gemini">Google Gemini 2.5 Flash (Recommended)</option>
              <option value="openai">OpenAI GPT-4o-mini</option>
              <option value="anthropic">Anthropic Claude 3.5 Haiku</option>
              <option value="mock">Offline Mock / Heuristic Engine</option>
            </select>
          </div>

          <div>
            <label className="block font-semibold text-foreground mb-1">
              Daily LLM API Call Cap
            </label>
            <input
              type="number"
              min={10}
              max={1000}
              value={form.LLM_DAILY_CALL_CAP ?? 100}
              onChange={(e) => handleFieldChange('LLM_DAILY_CALL_CAP', parseInt(e.target.value) || 100)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground font-mono"
            />
          </div>

          <div className="sm:col-span-2">
            <label className="block font-semibold text-foreground mb-1">
              Default Service Pitch
            </label>
            <input
              type="text"
              value={form.DEFAULT_SERVICE || 'DevOps & CI/CD pipeline automation'}
              onChange={(e) => handleFieldChange('DEFAULT_SERVICE', e.target.value)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground"
            />
          </div>

          <div className="sm:col-span-2">
            <label className="block font-semibold text-foreground mb-1">
              Default Evidence Result / Case Study Metric
            </label>
            <input
              type="text"
              value={form.DEFAULT_EXAMPLE_RESULT || 'reduced build failures by 43% in 3 weeks'}
              onChange={(e) => handleFieldChange('DEFAULT_EXAMPLE_RESULT', e.target.value)}
              className="w-full h-8 px-3 bg-muted/40 border border-border rounded-md text-foreground"
            />
          </div>
        </div>
      </div>

      {/* 5. Live SMTP Mailbox Test */}
      <div className="bg-card border border-border rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between border-b border-border/60 pb-2.5">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-bold text-foreground">Real SMTP Mailbox Delivery Test</h2>
          </div>
          <span className="text-[11px] px-2 py-0.5 rounded-full font-medium bg-primary/10 text-primary">
            Instant Test
          </span>
        </div>

        <p className="text-xs text-muted-foreground">
          Send a real verification email to your personal inbox right now to test if your SMTP host, Google App Password, and network connection are working properly.
        </p>

        <div className="flex flex-col sm:flex-row gap-2.5">
          <input
            type="email"
            value={testEmail}
            onChange={(e) => setTestEmail(e.target.value)}
            placeholder="Enter your personal recipient email (e.g. you@gmail.com)"
            className="flex-1 h-9 px-3 bg-muted/40 border border-border rounded-md text-foreground text-xs font-mono"
          />
          <button
            type="button"
            disabled={!testEmail.trim() || sendTestMutation.isPending}
            onClick={() => sendTestMutation.mutate(testEmail.trim())}
            className="h-9 px-4 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md shadow-xs disabled:opacity-50 flex items-center justify-center gap-1.5 transition-colors cursor-pointer shrink-0"
          >
            {sendTestMutation.isPending ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" /> Sending...
              </>
            ) : (
              <>
                <Send className="w-3.5 h-3.5" /> Send Test Email
              </>
            )}
          </button>
        </div>

        {testStatus && (
          <div
            className={`p-3 rounded-lg border text-xs flex items-start gap-2.5 ${
              testStatus.success
                ? 'bg-emerald-500/10 border-emerald-500/20 text-emerald-400'
                : 'bg-destructive/10 border-destructive/20 text-destructive'
            }`}
          >
            {testStatus.success ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5 text-emerald-500" />
            ) : (
              <AlertCircle className="w-4 h-4 shrink-0 mt-0.5 text-destructive" />
            )}
            <div>
              <p className="font-semibold">
                {testStatus.success ? 'Delivery Confirmed!' : 'SMTP Error Encountered'}
              </p>
              <p className="text-[11px] opacity-90 mt-0.5 font-mono break-all">{testStatus.message}</p>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
