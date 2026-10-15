import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  MessageSquare,
  Trophy,
  CheckCircle,
  Inbox,
  RefreshCw,
  User,
  Clock,
  ThumbsUp,
  Mail,
  Layers,
} from 'lucide-react'
import { api } from '@/api/client'
import type { Reply } from '@/api/types'
import { StatusChip } from '@/design/StatusChip'
import { useToast } from '@/design/Toast'
import { EmptyState, Skeleton } from '@/design/Feedback'

export function RepliesPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()

  const selectedInbox = searchParams.get('to_email') || ''
  const targetReplyId = searchParams.get('reply_id') ? Number(searchParams.get('reply_id')) : null

  const [filterClass, setFilterClass] = useState<string>('')
  const [handledIds, setHandledIds] = useState<number[]>(() => {
    try {
      const stored = localStorage.getItem('coldreach_handled_reply_ids')
      return stored ? JSON.parse(stored) : []
    } catch {
      return []
    }
  })
  const [wonIds, setWonIds] = useState<number[]>(() => {
    try {
      const stored = localStorage.getItem('coldreach_won_reply_ids')
      return stored ? JSON.parse(stored) : []
    } catch {
      return []
    }
  })

  // 1. Fetch Inbound Inboxes (accounts receiving replies)
  const { data: inboxesData } = useQuery({
    queryKey: ['replyInboxes'],
    queryFn: () => api.replies.inboxes(),
    refetchInterval: 10000,
  })

  // 2. Fetch All Configured Mailboxes (for tabs)
  const { data: mailboxes = [] } = useQuery({
    queryKey: ['mailboxes'],
    queryFn: () => api.mailboxes.list(),
  })

  // 3. Fetch Inbound Replies for Selected Inbox & Filter
  const {
    data: repliesData,
    isLoading,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['replies', filterClass, selectedInbox],
    queryFn: () =>
      api.replies.list({
        page: 1,
        page_size: 50,
        classification: filterClass || undefined,
        to_email: selectedInbox || undefined,
      }),
    refetchInterval: 3000,
  })

  // Auto scroll to targetReplyId if opened from notification bell
  useEffect(() => {
    if (targetReplyId && !isLoading) {
      const el = document.getElementById(`reply-${targetReplyId}`)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'center' })
      }
    }
  }, [targetReplyId, isLoading, repliesData])

  // Mutations
  const markWonMutation = useMutation({
    mutationFn: (id: number) => api.replies.markWon(id),
    onSuccess: (_data, id) => {
      setWonIds((prev) => {
        if (prev.includes(id)) return prev
        const updated = [...prev, id]
        try {
          localStorage.setItem('coldreach_won_reply_ids', JSON.stringify(updated))
        } catch {}
        return updated
      })
      toast({
        title: '🎉 Deal Marked as Won!',
        message: 'Lead converted successfully and funnel stats updated.',
        type: 'success',
      })
      queryClient.invalidateQueries({ queryKey: ['replies'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['funnel'] })
    },
    onError: (err: any) => {
      toast({ title: 'Action Failed', message: err.message, type: 'error' })
    },
  })

  const markHandledMutation = useMutation({
    mutationFn: (id: number) => api.replies.markHandled(id),
    onSuccess: (_data, id) => {
      setHandledIds((prev) => {
        if (prev.includes(id)) return prev
        const updated = [...prev, id]
        try {
          localStorage.setItem('coldreach_handled_reply_ids', JSON.stringify(updated))
        } catch {}
        return updated
      })
      toast({ title: 'Reply Marked as Reviewed', message: 'Reply status updated.', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['replies'] })
      queryClient.invalidateQueries({ queryKey: ['recentRepliesNotifications'] })
    },
    onError: (err: any) => {
      toast({ title: 'Action Failed', message: err.message, type: 'error' })
    },
  })

  const replies: Reply[] = repliesData?.items || []

  // Combine known mailboxes and active reply inboxes
  const inboxMap = new Map<string, number>()
  if (Array.isArray(inboxesData)) {
    for (const item of inboxesData) {
      if (item.email) {
        inboxMap.set(item.email.toLowerCase(), item.replies_count)
      }
    }
  }
  const totalInboundReplies = Array.isArray(inboxesData)
    ? inboxesData.reduce((acc, curr) => acc + (curr.replies_count || 0), 0)
    : 0

  // All unique mailbox emails
  const allInboxes = Array.from(
    new Set([
      ...mailboxes.map((m) => m.email.toLowerCase()),
      ...Array.from(inboxMap.keys()),
    ])
  )

  const handleSelectInbox = (inboxEmail: string) => {
    const nextParams = new URLSearchParams(searchParams)
    if (!inboxEmail) {
      nextParams.delete('to_email')
    } else {
      nextParams.set('to_email', inboxEmail)
    }
    // Remove specific reply_id on manual tab switch so highlight clears
    nextParams.delete('reply_id')
    setSearchParams(nextParams)
  }

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-foreground">Inbound Replies Inbox</h1>
            {repliesData?.total !== undefined && repliesData.total > 0 && (
              <span className="px-2 py-0.5 text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 rounded-full border border-emerald-500/20">
                {repliesData.total} Received
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Auto-captured from your IMAP inboxes. AI categorizes sentiment and automatically halts sequence follow-ups.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Classification Filter */}
          <select
            value={filterClass}
            onChange={(e) => setFilterClass(e.target.value)}
            className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground cursor-pointer font-medium"
          >
            <option value="">All Sentiment Categories</option>
            <option value="positive">🟢 Positive / Interested</option>
            <option value="other">⚪ General / Other</option>
            <option value="question">❓ Question / Inquiry</option>
            <option value="out_of_office">🟡 Out of Office (OOO)</option>
            <option value="not_interested">🔴 Not Interested</option>
            <option value="unsubscribe">🚫 Unsubscribe</option>
          </select>

          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Refresh Inbox"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* Account / Inbox Switcher Tabs */}
      {allInboxes.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 bg-card/70 border border-border rounded-xl p-2 shadow-xs">
          <div className="flex items-center gap-1.5 px-2 text-xs font-semibold text-muted-foreground shrink-0">
            <Mail className="w-3.5 h-3.5 text-primary" />
            <span>Switch Inbox:</span>
          </div>

          <div className="flex items-center gap-1.5 overflow-x-auto pb-1 sm:pb-0 scrollbar-none flex-1">
            {/* All Inboxes Tab */}
            <button
              type="button"
              onClick={() => handleSelectInbox('')}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all cursor-pointer whitespace-nowrap shrink-0 border ${
                !selectedInbox
                  ? 'bg-primary text-primary-foreground border-primary shadow-xs'
                  : 'bg-muted/40 hover:bg-muted text-muted-foreground border-border/60'
              }`}
            >
              <Layers className="w-3.5 h-3.5" />
              <span>All Inboxes</span>
              {totalInboundReplies > 0 && (
                <span
                  className={`px-1.5 py-0.2 rounded-full text-[10px] font-bold ${
                    !selectedInbox
                      ? 'bg-primary-foreground/20 text-primary-foreground'
                      : 'bg-muted text-foreground'
                  }`}
                >
                  {totalInboundReplies}
                </span>
              )}
            </button>

            {/* Individual Mailbox Tabs */}
            {allInboxes.map((inboxEmail) => {
              const count = inboxMap.get(inboxEmail) || 0
              const isSelected = selectedInbox.toLowerCase() === inboxEmail.toLowerCase()

              return (
                <button
                  key={inboxEmail}
                  type="button"
                  onClick={() => handleSelectInbox(inboxEmail)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium flex items-center gap-1.5 transition-all cursor-pointer whitespace-nowrap shrink-0 border ${
                    isSelected
                      ? 'bg-primary text-primary-foreground border-primary font-semibold shadow-xs'
                      : 'bg-muted/40 hover:bg-muted text-muted-foreground border-border/60 hover:text-foreground'
                  }`}
                >
                  <Mail className="w-3 h-3" />
                  <span className="truncate max-w-[180px]">{inboxEmail}</span>
                  {count > 0 && (
                    <span
                      className={`px-1.5 py-0.2 rounded-full text-[10px] font-bold ${
                        isSelected
                          ? 'bg-primary-foreground/25 text-primary-foreground'
                          : 'bg-primary/10 text-primary'
                      }`}
                    >
                      {count}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Info Card Explaining Inbound Flow */}
      <div className="p-3.5 bg-card/60 border border-border rounded-lg flex items-start gap-3 text-xs">
        <Inbox className="w-4 h-4 text-primary shrink-0 mt-0.5" />
        <div className="space-y-1 text-muted-foreground">
          <span className="font-semibold text-foreground">Multi-Inbox Sync & Follow-ups:</span>
          <p>
            Each prospect’s reply is routed back to the exact mailbox that contacted them. When an incoming email is detected via IMAP, it appears under that account above, and the sequence automatically pauses for that lead.
          </p>
        </div>
      </div>

      {/* Main Replies List */}
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-28 w-full rounded-lg" />
        </div>
      ) : replies.length === 0 ? (
        <EmptyState
          icon={<MessageSquare className="w-7 h-7 text-indigo-500" />}
          title={selectedInbox ? `No replies in ${selectedInbox}` : 'No Inbound Replies Yet'}
          description={
            selectedInbox
              ? `There are currently no inbound responses received on ${selectedInbox}. Try switching to another inbox or All Inboxes.`
              : 'Your outreach sequence is currently active. As recipients reply, their responses will automatically show up here with AI sentiment classification.'
          }
        />
      ) : (
        <div className="grid gap-3.5">
          {replies.map((rep) => {
            const isPositive = rep.is_positive_hint || rep.classification === 'positive'
            const isTarget = rep.id === targetReplyId

            return (
              <div
                id={`reply-${rep.id}`}
                key={rep.id}
                className={`bg-card border rounded-lg p-4 space-y-3 transition-all ${
                  isTarget
                    ? 'ring-2 ring-primary border-primary shadow-md bg-primary/5'
                    : isPositive
                    ? 'border-emerald-300 dark:border-emerald-800/80 bg-emerald-500/5'
                    : 'border-border'
                }`}
              >
                {/* Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border/50 pb-2.5">
                  <div className="flex items-center gap-3">
                    <div
                      className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-xs shrink-0 ${
                        isPositive
                          ? 'bg-emerald-500/20 text-emerald-600 dark:text-emerald-400'
                          : 'bg-muted text-muted-foreground'
                      }`}
                    >
                      <User className="w-4 h-4" />
                    </div>
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-foreground text-sm">{rep.from_email}</span>
                        <StatusChip
                          status={rep.classification}
                          variant={isPositive ? 'success' : rep.classification === 'out_of_office' ? 'warning' : 'neutral'}
                        />
                        {isPositive && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-bold bg-emerald-100 dark:bg-emerald-950 text-emerald-700 dark:text-emerald-300 rounded-sm">
                            <ThumbsUp className="w-3 h-3" /> High Intent
                          </span>
                        )}

                        {/* Recipient Inbox Badge */}
                        {rep.to_email && (
                          <span
                            onClick={() => handleSelectInbox(rep.to_email!)}
                            title={`Click to filter replies to ${rep.to_email}`}
                            className="inline-flex items-center gap-1 px-2 py-0.5 text-[10px] font-medium bg-muted border border-border/80 text-muted-foreground rounded hover:text-foreground cursor-pointer transition-colors"
                          >
                            <Mail className="w-2.5 h-2.5 text-primary" /> to: {rep.to_email}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-muted-foreground flex items-center gap-2 mt-0.5 flex-wrap">
                        <span className="font-medium text-foreground">{rep.subject}</span>
                        <span>•</span>
                        <span className="tabular-nums flex items-center gap-1">
                          <Clock className="w-3 h-3" />
                          {new Date(rep.created_at).toLocaleString()}
                        </span>
                      </div>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-2 self-end sm:self-center">
                    {rep.lead_id && (
                      wonIds.includes(rep.id) ? (
                        <span className="px-2.5 py-1 text-xs font-semibold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 rounded-md flex items-center gap-1 shadow-2xs">
                          <Trophy className="w-3.5 h-3.5 text-amber-500" /> Won Customer
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={markWonMutation.isPending}
                          onClick={() => markWonMutation.mutate(rep.id)}
                          className="px-2.5 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1 shadow-xs transition-colors cursor-pointer"
                          title="Mark lead as converted/won customer"
                        >
                          <Trophy className="w-3.5 h-3.5" /> Mark Won
                        </button>
                      )
                    )}

                    {handledIds.includes(rep.id) ? (
                      <span className="px-2.5 py-1 text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 rounded-md flex items-center gap-1 shadow-2xs">
                        <CheckCircle className="w-3.5 h-3.5" /> Reviewed
                      </span>
                    ) : (
                      <button
                        type="button"
                        disabled={markHandledMutation.isPending}
                        onClick={() => markHandledMutation.mutate(rep.id)}
                        className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted text-muted-foreground hover:text-foreground rounded-md flex items-center gap-1 transition-colors cursor-pointer"
                      >
                        <CheckCircle className="w-3.5 h-3.5" /> Mark Reviewed
                      </button>
                    )}
                  </div>
                </div>

                {/* Reply Body Snippet */}
                <div className="p-3 bg-muted/40 rounded-md border border-border/60 text-xs font-sans whitespace-pre-wrap leading-relaxed text-foreground">
                  {rep.body || rep.snippet}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
