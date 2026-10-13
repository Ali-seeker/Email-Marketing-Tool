import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  CheckSquare,
  Check,
  X,
  Sparkles,
  Edit2,
  ExternalLink,
  Loader2,
  RefreshCw,
  Building,
  Quote,
  CheckCheck,
} from 'lucide-react'
import { api } from '@/api/client'
import type { ApprovalQueueItem } from '@/api/types'
import { useToast } from '@/design/Toast'
import { EmptyState, Skeleton } from '@/design/Feedback'

export function ApprovalPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [editingItem, setEditingItem] = useState<{ id: number; note: string } | null>(null)

  // Fetch pending approval items
  const { data: pendingData, isLoading, refetch, isFetching } = useQuery({
    queryKey: ['pendingApprovals'],
    queryFn: () => api.approval.listPending({ page: 1, page_size: 100 }),
  })

  // Approve single lead
  const approveMutation = useMutation({
    mutationFn: (leadId: number) => api.approval.approve(leadId),
    onSuccess: () => {
      toast({ title: 'Lead Approved', message: 'Lead enrolled and ready for dispatch', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Approval Failed', message: err.message, type: 'error' })
    },
  })

  // Batch approve all leads
  const approveManyMutation = useMutation({
    mutationFn: (leadIds: number[]) => api.approval.approveMany(leadIds),
    onSuccess: (data) => {
      toast({
        title: 'Batch Approved',
        message: `Approved ${data.approved} leads successfully!`,
        type: 'success',
      })
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Batch Approval Failed', message: err.message, type: 'error' })
    },
  })

  // Edit and approve lead
  const editAndApproveMutation = useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) =>
      api.approval.editAndApprove(id, note),
    onSuccess: () => {
      toast({ title: 'Note Updated & Approved', type: 'success' })
      setEditingItem(null)
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Edit & Approve Failed', message: err.message, type: 'error' })
    },
  })

  // Reject lead
  const rejectMutation = useMutation({
    mutationFn: (leadId: number) => api.approval.reject(leadId, 'Rejected during manual review'),
    onSuccess: () => {
      toast({ title: 'Lead Rejected', message: 'Moved to Needs Manual Note', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Reject Failed', message: err.message, type: 'error' })
    },
  })

  // Regenerate lead draft
  const regenerateMutation = useMutation({
    mutationFn: (leadId: number) => api.approval.regenerate(leadId),
    onSuccess: () => {
      toast({ title: 'Regenerating Draft', message: 'Re-analyzed website and generated fresh note', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
    },
    onError: (err: any) => {
      toast({ title: 'Regeneration Failed', message: err.message, type: 'error' })
    },
  })

  const items: ApprovalQueueItem[] = pendingData?.items || []

  return (
    <div className="space-y-4">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-foreground">Approval Queue</h1>
            {items.length > 0 && (
              <span className="px-2 py-0.5 text-xs font-semibold bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 rounded-full border border-indigo-500/20">
                {items.length} Pending
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground mt-0.5">
            Review AI-personalized opening observations and grounded evidence before emails are dispatched.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => refetch()}
            disabled={isFetching}
            className="p-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
            title="Refresh Queue"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          </button>

          {items.length > 0 && (
            <button
              type="button"
              disabled={approveManyMutation.isPending}
              onClick={() => approveManyMutation.mutate(items.map((i) => i.lead_id))}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md shadow-xs transition-colors disabled:opacity-60 cursor-pointer"
            >
              {approveManyMutation.isPending ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <CheckCheck className="w-3.5 h-3.5" />
              )}
              Approve All ({items.length})
            </button>
          )}
        </div>
      </div>

      {/* Main Content Area */}
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-28 w-full rounded-lg" />
          <Skeleton className="h-28 w-full rounded-lg" />
        </div>
      ) : items.length === 0 ? (
        <div className="space-y-4">
          <EmptyState
            icon={<CheckSquare className="w-7 h-7 text-emerald-500" />}
            title="Approval Queue is Clear — All Drafts Approved!"
            description="All personalized drafts have been approved. Approved leads are enrolled into their 4-step sequence and scheduled for safe dispatch."
            action={
              <a
                href="/campaigns"
                className="inline-flex items-center gap-1.5 px-3.5 py-1.5 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-md shadow-xs transition-colors cursor-pointer"
              >
                View Campaigns & Outbound Schedule
              </a>
            }
          />
        </div>
      ) : (
        <div className="grid gap-3.5">
          {items.map((item) => {
            const isEditing = editingItem?.id === item.lead_id
            const isPendingAction =
              approveMutation.isPending ||
              rejectMutation.isPending ||
              regenerateMutation.isPending ||
              editAndApproveMutation.isPending

            return (
              <div
                key={item.lead_id}
                className="bg-card border border-border rounded-lg p-4 space-y-3 transition-shadow hover:shadow-xs"
              >
                {/* Lead Header Info */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-border/50 pb-2.5">
                  <div className="flex items-center gap-3">
                    <div className="w-8 h-8 rounded-full bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center font-bold text-xs shrink-0">
                      {(item.company || item.email).charAt(0).toUpperCase()}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-foreground text-sm">{item.email}</span>
                        {item.campaign_name && (
                          <span className="text-[10px] px-2 py-0.5 rounded-sm bg-muted text-muted-foreground border border-border">
                            {item.campaign_name}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5">
                        {item.company && (
                          <span className="flex items-center gap-1">
                            <Building className="w-3 h-3" /> {item.company}
                          </span>
                        )}
                        {item.website && (
                          <a
                            href={item.website}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 text-primary hover:underline"
                          >
                            <ExternalLink className="w-3 h-3" /> {item.website}
                          </a>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="flex items-center gap-1.5 self-end sm:self-center">
                    <button
                      type="button"
                      disabled={isPendingAction}
                      onClick={() =>
                        setEditingItem(
                          isEditing
                            ? null
                            : { id: item.lead_id, note: item.personal_note || '' }
                        )
                      }
                      className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted rounded-md flex items-center gap-1 transition-colors cursor-pointer"
                      title="Edit note before approving"
                    >
                      <Edit2 className="w-3 h-3 text-muted-foreground" /> Edit
                    </button>

                    <button
                      type="button"
                      disabled={isPendingAction}
                      onClick={() => regenerateMutation.mutate(item.lead_id)}
                      className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted text-indigo-600 dark:text-indigo-400 rounded-md flex items-center gap-1 transition-colors cursor-pointer"
                      title="Regenerate draft observation with AI"
                    >
                      <Sparkles className="w-3 h-3" /> Regenerate
                    </button>

                    <button
                      type="button"
                      disabled={isPendingAction}
                      onClick={() => rejectMutation.mutate(item.lead_id)}
                      className="px-2.5 py-1 text-xs font-medium border border-red-200 dark:border-red-900/60 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/30 rounded-md flex items-center gap-1 transition-colors cursor-pointer"
                      title="Reject draft"
                    >
                      <X className="w-3 h-3" /> Reject
                    </button>

                    <button
                      type="button"
                      disabled={isPendingAction}
                      onClick={() => approveMutation.mutate(item.lead_id)}
                      className="px-3 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1 shadow-xs transition-colors cursor-pointer"
                      title="Approve and enroll lead"
                    >
                      <Check className="w-3.5 h-3.5" /> Approve
                    </button>
                  </div>
                </div>

                {/* Personal Note Draft */}
                <div className="space-y-1.5">
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground flex items-center gap-1">
                    <Sparkles className="w-3 h-3 text-indigo-500" /> Opening Observation (AI Note)
                  </span>

                  {isEditing ? (
                    <div className="space-y-2">
                      <textarea
                        value={editingItem?.note}
                        onChange={(e) =>
                          setEditingItem({ id: item.lead_id, note: e.target.value })
                        }
                        rows={2}
                        className="w-full text-xs p-2.5 rounded-md border border-primary bg-background text-foreground focus:outline-hidden"
                        placeholder="Edit personal note observation..."
                      />
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          disabled={!editingItem?.note.trim() || isPendingAction}
                          onClick={() =>
                            editAndApproveMutation.mutate({
                              id: item.lead_id,
                              note: editingItem!.note,
                            })
                          }
                          className="px-3 py-1 text-xs font-semibold bg-primary text-primary-foreground rounded-md cursor-pointer hover:bg-primary/90"
                        >
                          Save & Approve
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingItem(null)}
                          className="px-2.5 py-1 text-xs border border-border rounded-md hover:bg-muted text-muted-foreground cursor-pointer"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <p className="text-xs text-foreground bg-muted/40 p-2.5 rounded-md border border-border/60 leading-relaxed font-normal">
                      "{item.personal_note || 'No observation note generated yet.'}"
                    </p>
                  )}
                </div>

                {/* Evidence Quote */}
                {item.evidence_quote && (
                  <div className="pt-1 text-[11px] text-muted-foreground flex items-start gap-1.5">
                    <Quote className="w-3.5 h-3.5 text-indigo-500/70 shrink-0 mt-0.5" />
                    <span className="italic">
                      <strong>Grounded Evidence Quote:</strong> "{item.evidence_quote}"
                    </span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
