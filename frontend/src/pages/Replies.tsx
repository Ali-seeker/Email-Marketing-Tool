import { useState } from 'react'
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
} from 'lucide-react'
import { api } from '@/api/client'
import type { Reply } from '@/api/types'
import { StatusChip } from '@/design/StatusChip'
import { useToast } from '@/design/Toast'
import { EmptyState, Skeleton } from '@/design/Feedback'

export function RepliesPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [filterClass, setFilterClass] = useState<string>('')

  // 1. Fetch Inbound Replies
  const {
    data: repliesData,
    isLoading,
    refetch,
    isFetching,
  } = useQuery({
    queryKey: ['replies', filterClass],
    queryFn: () =>
      api.replies.list({
        page: 1,
        page_size: 50,
        classification: filterClass || undefined,
      }),
  })

  // Mutations
  const markWonMutation = useMutation({
    mutationFn: (id: number) => api.replies.markWon(id),
    onSuccess: () => {
      toast({
        title: '🎉 Deal Marked as Won!',
        message: 'Lead converted successfully and status updated.',
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
    onSuccess: () => {
      toast({ title: 'Reply Handled', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['replies'] })
    },
    onError: (err: any) => {
      toast({ title: 'Action Failed', message: err.message, type: 'error' })
    },
  })

  const replies: Reply[] = repliesData?.items || []

  return (
    <div className="space-y-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-foreground">Inbound Replies Inbox</h1>
            {replies.length > 0 && (
              <span className="px-2 py-0.5 text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 rounded-full border border-emerald-500/20">
                {replies.length} Received
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Auto-captured from your IMAP mailbox. AI categorizes sentiment and automatically halts sequence follow-ups.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {/* Classification Filter */}
          <select
            value={filterClass}
            onChange={(e) => setFilterClass(e.target.value)}
            className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground cursor-pointer"
          >
            <option value="">All Sentiment Categories</option>
            <option value="positive">Positive / Interested</option>
            <option value="question">Information / Question</option>
            <option value="out_of_office">Out of Office (OOO)</option>
            <option value="not_interested">Not Interested</option>
            <option value="unsubscribe">Unsubscribe</option>
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

      {/* Info Card Explaining Inbound Flow */}
      <div className="p-3.5 bg-card/60 border border-border rounded-lg flex items-start gap-3 text-xs">
        <Inbox className="w-4 h-4 text-primary shrink-0 mt-0.5" />
        <div className="space-y-1 text-muted-foreground">
          <span className="font-semibold text-foreground">How Reply Syncing Works:</span>
          <p>
            When a prospect replies to your cold email, their reply arrives directly in your <strong>actual mailbox / phone app</strong> (Gmail, Outlook, etc.) because your email is the reply-to. Simultaneously, the background worker detects the incoming email via IMAP, flags the sentiment here, and immediately marks the lead <code>replied</code> so no automated follow-up steps are sent.
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
          title="No Inbound Replies Yet"
          description="Your outreach sequence is currently active. As recipients reply, their responses will automatically show up here with AI sentiment classification."
        />
      ) : (
        <div className="grid gap-3.5">
          {replies.map((rep) => {
            const isPositive = rep.is_positive_hint || rep.classification === 'positive'

            return (
              <div
                key={rep.id}
                className={`bg-card border rounded-lg p-4 space-y-3 transition-shadow hover:shadow-xs ${
                  isPositive
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
                      <div className="flex items-center gap-2">
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
                      </div>
                      <div className="text-xs text-muted-foreground flex items-center gap-2 mt-0.5">
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
                      <button
                        type="button"
                        disabled={markWonMutation.isPending}
                        onClick={() => markWonMutation.mutate(rep.lead_id!)}
                        className="px-2.5 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1 shadow-xs transition-colors cursor-pointer"
                        title="Mark lead as converted/won customer"
                      >
                        <Trophy className="w-3.5 h-3.5" /> Mark Won
                      </button>
                    )}

                    <button
                      type="button"
                      disabled={markHandledMutation.isPending}
                      onClick={() => markHandledMutation.mutate(rep.id)}
                      className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted text-muted-foreground hover:text-foreground rounded-md flex items-center gap-1 transition-colors cursor-pointer"
                    >
                      <CheckCircle className="w-3.5 h-3.5" /> Mark Reviewed
                    </button>
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
