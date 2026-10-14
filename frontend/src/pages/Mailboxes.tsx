import React, { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Mail,
  Plus,
  RefreshCw,
  Server,
  Shield,
  Send,
  Trash2,
  Edit2,
  Eye,
  EyeOff,
  Zap,
  X,
  ExternalLink,
} from 'lucide-react'
import { api } from '@/api/client'
import type { Mailbox, MailboxCreate } from '@/api/types'
import { useToast } from '@/design/Toast'
import { EmptyState, Skeleton } from '@/design/Feedback'

export function MailboxesPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const [isAddModalOpen, setIsAddModalOpen] = useState(false)
  const [editingMailbox, setEditingMailbox] = useState<Mailbox | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)

  // 1. Fetch Mailboxes
  const {
    data: mailboxes = [],
    isLoading,
    isFetching,
    refetch,
  } = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => api.mailboxes.list(),
  })

  // 2. Mutations
  const createMutation = useMutation({
    mutationFn: (payload: MailboxCreate) => api.mailboxes.create(payload),
    onSuccess: (newMb) => {
      toast({
        title: 'Mailbox Connected',
        message: `${newMb.email} added to sending rotation pool.`,
        type: 'success',
      })
      setIsAddModalOpen(false)
      queryClient.invalidateQueries({ queryKey: ['mailboxes'] })
    },
    onError: (err: any) => {
      toast({
        title: 'Connection Failed',
        message: err.message || 'Could not save mailbox credentials.',
        type: 'error',
      })
    },
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<MailboxCreate> }) =>
      api.mailboxes.update(id, data),
    onSuccess: () => {
      toast({ title: 'Mailbox Updated', type: 'info' })
      setEditingMailbox(null)
      queryClient.invalidateQueries({ queryKey: ['mailboxes'] })
    },
    onError: (err: any) => {
      toast({ title: 'Update Failed', message: err.message, type: 'error' })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: number) => api.mailboxes.delete(id),
    onSuccess: () => {
      toast({ title: 'Mailbox Removed', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['mailboxes'] })
    },
    onError: (err: any) => {
      toast({ title: 'Delete Failed', message: err.message, type: 'error' })
    },
  })

  const testConnection = async (id: number) => {
    setTestingId(id)
    try {
      const res = await api.mailboxes.test(id)
      if (res.success) {
        toast({
          title: '✅ Connection Verified',
          message: res.detail || `Test email successfully sent to ${res.recipient}!`,
          type: 'success',
        })
      } else {
        toast({
          title: '❌ Connection Failed',
          message: res.detail || 'Could not verify mailbox credentials.',
          type: 'error',
        })
      }
    } catch (err: any) {
      toast({
        title: 'Connection Test Failed',
        message: err.message || 'Failed to connect to mail server.',
        type: 'error',
      })
    } finally {
      setTestingId(null)
    }
  }

  // Summary statistics
  const totalCount = mailboxes.length
  const activeCount = mailboxes.filter((m) => m.is_active).length
  const totalCapacity = mailboxes.reduce((acc, m) => acc + (m.is_active ? m.daily_send_cap : 0), 0)
  const totalSentToday = mailboxes.reduce((acc, m) => acc + (m.sent_today || 0), 0)
  const remainingToday = Math.max(0, totalCapacity - totalSentToday)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2.5">
            <h1 className="text-xl font-bold tracking-tight text-foreground">
              Sending Mailboxes & Rotation
            </h1>
            <span className="px-2 py-0.5 text-xs font-semibold bg-primary/10 text-primary rounded-full border border-primary/20">
              {activeCount} / {totalCount} Active
            </span>
          </div>
          <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
            Initial reachouts (Step 1) are dynamically shuffled across your active mailboxes to balance sender reputation. Follow-ups strictly stick to the original reachout mailbox.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-2 text-xs font-medium border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Refresh mailboxes"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          </button>
          <button
            type="button"
            onClick={() => setIsAddModalOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
          >
            <Plus className="w-3.5 h-3.5" /> Connect Mailbox
          </button>
        </div>
      </div>

      {/* Stats Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-4 bg-card border border-border rounded-xl shadow-xs">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-xs font-medium">Active Inboxes</span>
            <Mail className="w-4 h-4 text-primary" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-foreground">{activeCount}</span>
            <span className="text-xs text-muted-foreground">of {totalCount} total</span>
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">Ready for round-robin dispatch</p>
        </div>

        <div className="p-4 bg-card border border-border rounded-xl shadow-xs">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-xs font-medium">Daily Send Capacity</span>
            <Zap className="w-4 h-4 text-amber-500" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-foreground">{totalCapacity}</span>
            <span className="text-xs text-muted-foreground">emails / day</span>
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">Aggregated across active accounts</p>
        </div>

        <div className="p-4 bg-card border border-border rounded-xl shadow-xs">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-xs font-medium">Sent Today</span>
            <Send className="w-4 h-4 text-emerald-500" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-foreground">{totalSentToday}</span>
            <span className="text-xs text-muted-foreground">sent</span>
          </div>
          <div className="w-full bg-muted rounded-full h-1.5 mt-2 overflow-hidden">
            <div
              className="bg-emerald-500 h-1.5 rounded-full transition-all duration-300"
              style={{
                width: `${totalCapacity > 0 ? Math.min(100, (totalSentToday / totalCapacity) * 100) : 0}%`,
              }}
            />
          </div>
        </div>

        <div className="p-4 bg-card border border-border rounded-xl shadow-xs">
          <div className="flex items-center justify-between text-muted-foreground">
            <span className="text-xs font-medium">Remaining Quota</span>
            <Shield className="w-4 h-4 text-indigo-500" />
          </div>
          <div className="mt-2 flex items-baseline gap-2">
            <span className="text-2xl font-bold tracking-tight text-foreground">{remainingToday}</span>
            <span className="text-xs text-muted-foreground">remaining</span>
          </div>
          <p className="text-[11px] text-muted-foreground mt-1">Warmup safety headroom today</p>
        </div>
      </div>

      {/* Mailboxes Grid */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-56 rounded-xl" />
          <Skeleton className="h-56 rounded-xl" />
        </div>
      ) : mailboxes.length === 0 ? (
        <EmptyState
          icon={<Mail className="w-8 h-8 text-primary" />}
          title="No Sending Mailboxes Connected"
          description="Connect your first Google Workspace or custom SMTP inbox to start sending cold campaigns with round-robin shuffle."
          action={
            <button
              type="button"
              onClick={() => setIsAddModalOpen(true)}
              className="px-4 py-2 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
            >
              <Plus className="w-4 h-4" /> Connect Mailbox
            </button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {mailboxes.map((mb) => {
            const usagePercent =
              mb.daily_send_cap > 0
                ? Math.min(100, Math.round(((mb.sent_today || 0) / mb.daily_send_cap) * 100))
                : 0
            const isTesting = testingId === mb.id

            return (
              <div
                key={mb.id}
                className={`bg-card border rounded-xl p-5 flex flex-col justify-between transition-all shadow-xs hover:shadow-md ${
                  mb.is_active ? 'border-border' : 'border-border/60 opacity-75 bg-muted/20'
                }`}
              >
                <div>
                  {/* Top Bar: Status Chip & Active Toggle */}
                  <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-3">
                    <div className="flex items-center gap-2">
                      <span
                        className={`w-2.5 h-2.5 rounded-full ${
                          mb.is_active ? 'bg-emerald-500 animate-pulse' : 'bg-muted-foreground'
                        }`}
                      />
                      <span className="text-xs font-semibold text-foreground">
                        {mb.is_active ? 'Active Sender' : 'Paused'}
                      </span>
                    </div>

                    <div className="flex items-center gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          updateMutation.mutate({
                            id: mb.id,
                            data: { is_active: !mb.is_active },
                          })
                        }
                        title={mb.is_active ? 'Pause mailbox' : 'Activate mailbox'}
                        className={`text-xs px-2 py-0.5 rounded-full font-medium transition-colors cursor-pointer border ${
                          mb.is_active
                            ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20 hover:bg-emerald-500/20'
                            : 'bg-muted text-muted-foreground border-border hover:bg-muted/80'
                        }`}
                      >
                        {mb.is_active ? 'Disable' : 'Enable'}
                      </button>
                    </div>
                  </div>

                  {/* Mailbox Info */}
                  <div className="mt-3.5 space-y-1">
                    <div className="flex items-center gap-2">
                      <div className="w-8 h-8 rounded-lg bg-primary/10 text-primary flex items-center justify-center font-bold text-xs shrink-0">
                        {mb.email.charAt(0).toUpperCase()}
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-bold text-foreground truncate" title={mb.email}>
                          {mb.email}
                        </p>
                        <p className="text-xs text-muted-foreground truncate">{mb.display_name}</p>
                      </div>
                    </div>

                    {/* Server specs */}
                    <div className="pt-2 text-[11px] text-muted-foreground space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="flex items-center gap-1">
                          <Server className="w-3 h-3 text-muted-foreground/80" /> SMTP Host:
                        </span>
                        <span className="font-mono text-foreground/90">
                          {mb.smtp_host}:{mb.smtp_port}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Daily Sending Quota Progress */}
                  <div className="mt-4 p-3 bg-muted/40 rounded-lg border border-border/50">
                    <div className="flex items-center justify-between text-xs">
                      <span className="text-muted-foreground font-medium">Daily Send Cap</span>
                      <span className="font-bold text-foreground tabular-nums">
                        {mb.sent_today || 0} / {mb.daily_send_cap}
                      </span>
                    </div>

                    <div className="w-full bg-muted rounded-full h-1.5 mt-2 overflow-hidden">
                      <div
                        className={`h-1.5 rounded-full transition-all duration-300 ${
                          usagePercent > 90
                            ? 'bg-rose-500'
                            : usagePercent > 70
                            ? 'bg-amber-500'
                            : 'bg-primary'
                        }`}
                        style={{ width: `${usagePercent}%` }}
                      />
                    </div>

                    <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
                      <span>{mb.remaining_today ?? Math.max(0, mb.daily_send_cap - (mb.sent_today || 0))} remaining today</span>
                      <span className="font-semibold">{usagePercent}% used</span>
                    </div>
                  </div>
                </div>

                {/* Card Actions Bottom */}
                <div className="mt-4 pt-3 border-t border-border/60 flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => testConnection(mb.id)}
                    disabled={isTesting}
                    className="px-2.5 py-1 text-xs font-semibold border border-border hover:bg-muted text-foreground rounded-md flex items-center gap-1.5 transition-colors cursor-pointer"
                  >
                    <RefreshCw className={`w-3 h-3 ${isTesting ? 'animate-spin text-primary' : ''}`} />
                    {isTesting ? 'Testing...' : 'Test Connection'}
                  </button>

                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={() => setEditingMailbox(mb)}
                      className="p-1.5 text-muted-foreground hover:text-foreground hover:bg-muted rounded-md transition-colors cursor-pointer"
                      title="Edit mailbox settings"
                    >
                      <Edit2 className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        if (confirm(`Are you sure you want to remove ${mb.email} from sending pool?`)) {
                          deleteMutation.mutate(mb.id)
                        }
                      }}
                      className="p-1.5 text-muted-foreground hover:text-rose-600 hover:bg-rose-500/10 rounded-md transition-colors cursor-pointer"
                      title="Remove mailbox"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Connect Mailbox Modal */}
      {isAddModalOpen && (
        <MailboxModal
          isOpen={isAddModalOpen}
          onClose={() => setIsAddModalOpen(false)}
          onSubmit={(data) => createMutation.mutate(data)}
          isLoading={createMutation.isPending}
        />
      )}

      {/* Edit Mailbox Modal */}
      {editingMailbox && (
        <EditMailboxModal
          mailbox={editingMailbox}
          onClose={() => setEditingMailbox(null)}
          onSubmit={(data) => updateMutation.mutate({ id: editingMailbox.id, data })}
          isLoading={updateMutation.isPending}
        />
      )}
    </div>
  )
}

// Modal for adding a new mailbox with provider presets
function MailboxModal({
  isOpen,
  onClose,
  onSubmit,
  isLoading,
}: {
  isOpen: boolean
  onClose: () => void
  onSubmit: (data: MailboxCreate) => void
  isLoading: boolean
}) {
  const [provider, setProvider] = useState<'gmail' | 'outlook' | 'custom'>('gmail')
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('ColdReach Operator')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [dailySendCap, setDailySendCap] = useState(30)
  const [showAdvanced, setShowAdvanced] = useState(false)

  // Advanced config
  const [smtpHost, setSmtpHost] = useState('smtp.gmail.com')
  const [smtpPort, setSmtpPort] = useState(465)
  const [imapHost, setImapHost] = useState('imap.gmail.com')
  const [imapPort, setImapPort] = useState(993)

  const handleProviderSelect = (p: 'gmail' | 'outlook' | 'custom') => {
    setProvider(p)
    if (p === 'gmail') {
      setSmtpHost('smtp.gmail.com')
      setSmtpPort(465)
      setImapHost('imap.gmail.com')
      setImapPort(993)
    } else if (p === 'outlook') {
      setSmtpHost('smtp.office365.com')
      setSmtpPort(587)
      setImapHost('outlook.office365.com')
      setImapPort(993)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSubmit({
      email: email.trim(),
      display_name: displayName.trim(),
      smtp_host: smtpHost.trim(),
      smtp_port: Number(smtpPort),
      smtp_username: email.trim(),
      smtp_password: password.trim(),
      imap_host: imapHost.trim(),
      imap_port: Number(imapPort),
      imap_username: email.trim(),
      imap_password: password.trim(),
      daily_send_cap: Number(dailySendCap),
      is_active: true,
    })
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-xs animate-in fade-in">
      <div className="bg-card border border-border w-full max-w-lg rounded-xl shadow-xl overflow-hidden animate-in zoom-in-95">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Mail className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-bold text-foreground">Connect Sending Mailbox</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          {/* Provider Preset Buttons */}
          <div>
            <label className="text-xs font-semibold text-foreground block mb-2">Provider Preset</label>
            <div className="grid grid-cols-3 gap-2">
              <button
                type="button"
                onClick={() => handleProviderSelect('gmail')}
                className={`py-2 px-3 text-xs font-semibold rounded-lg border text-center transition-all cursor-pointer ${
                  provider === 'gmail'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-muted/40 hover:bg-muted text-muted-foreground'
                }`}
              >
                Google / Gmail
              </button>
              <button
                type="button"
                onClick={() => handleProviderSelect('outlook')}
                className={`py-2 px-3 text-xs font-semibold rounded-lg border text-center transition-all cursor-pointer ${
                  provider === 'outlook'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-muted/40 hover:bg-muted text-muted-foreground'
                }`}
              >
                Outlook / 365
              </button>
              <button
                type="button"
                onClick={() => handleProviderSelect('custom')}
                className={`py-2 px-3 text-xs font-semibold rounded-lg border text-center transition-all cursor-pointer ${
                  provider === 'custom'
                    ? 'border-primary bg-primary/10 text-primary'
                    : 'border-border bg-muted/40 hover:bg-muted text-muted-foreground'
                }`}
              >
                Custom SMTP
              </button>
            </div>
          </div>

          {/* Email & Display Name */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-medium text-foreground block mb-1">Email Address *</label>
              <input
                type="email"
                required
                placeholder="name@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-full h-8 px-2.5 text-xs bg-background border border-border rounded-md text-foreground focus:outline-hidden focus:ring-1 focus:ring-primary"
              />
            </div>
            <div>
              <label className="text-xs font-medium text-foreground block mb-1">Sender Display Name *</label>
              <input
                type="text"
                required
                placeholder="e.g. Ali Hussnain"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="w-full h-8 px-2.5 text-xs bg-background border border-border rounded-md text-foreground focus:outline-hidden focus:ring-1 focus:ring-primary"
              />
            </div>
          </div>

          {/* Password / App Password */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-foreground">App Password / Password *</label>
              {provider === 'gmail' && (
                <a
                  href="https://myaccount.google.com/apppasswords"
                  target="_blank"
                  rel="noreferrer"
                  className="text-[10px] text-primary hover:underline flex items-center gap-0.5"
                >
                  Generate Google App Password <ExternalLink className="w-2.5 h-2.5" />
                </a>
              )}
            </div>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                required
                placeholder={provider === 'gmail' ? '16-character App Password' : 'Account password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full h-8 pl-2.5 pr-8 text-xs bg-background border border-border rounded-md text-foreground font-mono focus:outline-hidden focus:ring-1 focus:ring-primary"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2 top-2 text-muted-foreground hover:text-foreground"
              >
                {showPassword ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>
            <p className="text-[10px] text-muted-foreground mt-1">
              For Gmail/Workspace, 2-Step Verification must be on. Use an App Password (spaces will be stripped automatically).
            </p>
          </div>

          {/* Daily Send Cap */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-foreground">Daily Send Limit (Cap)</label>
              <span className="text-xs font-bold text-primary tabular-nums">{dailySendCap} emails/day</span>
            </div>
            <input
              type="range"
              min="5"
              max="100"
              step="5"
              value={dailySendCap}
              onChange={(e) => setDailySendCap(Number(e.target.value))}
              className="w-full accent-primary cursor-pointer"
            />
            <div className="flex justify-between text-[10px] text-muted-foreground">
              <span>Warmup (10)</span>
              <span>Recommended (30-40)</span>
              <span>Aggressive (100)</span>
            </div>
          </div>

          {/* Toggle Advanced Server Settings */}
          <div>
            <button
              type="button"
              onClick={() => setShowAdvanced(!showAdvanced)}
              className="text-xs font-medium text-primary hover:underline flex items-center gap-1 cursor-pointer"
            >
              {showAdvanced ? 'Hide Advanced Server Details' : 'Show Advanced Host & Ports'}
            </button>
          </div>

          {showAdvanced && (
            <div className="p-3 bg-muted/40 rounded-lg border border-border/60 space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground block mb-1">SMTP Host</label>
                  <input
                    type="text"
                    value={smtpHost}
                    onChange={(e) => setSmtpHost(e.target.value)}
                    className="w-full h-7 px-2 text-xs bg-background border border-border rounded text-foreground font-mono"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground block mb-1">SMTP Port</label>
                  <input
                    type="number"
                    value={smtpPort}
                    onChange={(e) => setSmtpPort(Number(e.target.value))}
                    className="w-full h-7 px-2 text-xs bg-background border border-border rounded text-foreground font-mono"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground block mb-1">IMAP Host</label>
                  <input
                    type="text"
                    value={imapHost}
                    onChange={(e) => setImapHost(e.target.value)}
                    className="w-full h-7 px-2 text-xs bg-background border border-border rounded text-foreground font-mono"
                  />
                </div>
                <div>
                  <label className="text-[11px] font-medium text-muted-foreground block mb-1">IMAP Port</label>
                  <input
                    type="number"
                    value={imapPort}
                    onChange={(e) => setImapPort(Number(e.target.value))}
                    className="w-full h-7 px-2 text-xs bg-background border border-border rounded text-foreground font-mono"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Modal Actions */}
          <div className="pt-2 flex items-center justify-end gap-2 border-t border-border">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium border border-border hover:bg-muted text-foreground rounded-md transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-4 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
            >
              {isLoading ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Connecting...
                </>
              ) : (
                'Connect Mailbox'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// Modal for editing an existing mailbox's display name and cap
function EditMailboxModal({
  mailbox,
  onClose,
  onSubmit,
  isLoading,
}: {
  mailbox: Mailbox
  onClose: () => void
  onSubmit: (data: Partial<MailboxCreate>) => void
  isLoading: boolean
}) {
  const [displayName, setDisplayName] = useState(mailbox.display_name)
  const [dailySendCap, setDailySendCap] = useState(mailbox.daily_send_cap)
  const [password, setPassword] = useState('')

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const payload: any = {
      display_name: displayName.trim(),
      daily_send_cap: Number(dailySendCap),
    }
    if (password.trim()) {
      payload.smtp_password = password.trim()
      payload.imap_password = password.trim()
    }
    onSubmit(payload)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-xs animate-in fade-in">
      <div className="bg-card border border-border w-full max-w-md rounded-xl shadow-xl overflow-hidden animate-in zoom-in-95">
        <div className="p-4 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Edit2 className="w-4 h-4 text-primary" />
            <h2 className="text-sm font-bold text-foreground">Edit {mailbox.email}</h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-5 space-y-4">
          <div>
            <label className="text-xs font-medium text-foreground block mb-1">Sender Display Name</label>
            <input
              type="text"
              required
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              className="w-full h-8 px-2.5 text-xs bg-background border border-border rounded-md text-foreground focus:outline-hidden focus:ring-1 focus:ring-primary"
            />
          </div>

          <div>
            <div className="flex items-center justify-between mb-1">
              <label className="text-xs font-medium text-foreground">Daily Send Limit (Cap)</label>
              <span className="text-xs font-bold text-primary tabular-nums">{dailySendCap} emails/day</span>
            </div>
            <input
              type="range"
              min="5"
              max="100"
              step="5"
              value={dailySendCap}
              onChange={(e) => setDailySendCap(Number(e.target.value))}
              className="w-full accent-primary cursor-pointer"
            />
          </div>

          <div>
            <label className="text-xs font-medium text-foreground block mb-1">
              New Password (Leave blank to keep existing)
            </label>
            <input
              type="password"
              placeholder="••••••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full h-8 px-2.5 text-xs bg-background border border-border rounded-md text-foreground font-mono focus:outline-hidden focus:ring-1 focus:ring-primary"
            />
          </div>

          <div className="pt-2 flex items-center justify-end gap-2 border-t border-border">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-xs font-medium border border-border hover:bg-muted text-foreground rounded-md transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isLoading}
              className="px-4 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
            >
              {isLoading ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
