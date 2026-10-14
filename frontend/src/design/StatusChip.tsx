import {
  CheckCircle2,
  AlertTriangle,
  XCircle,
  Clock,
  Send,
  MessageSquare,
  Trophy,
  ShieldAlert,
  HelpCircle,
  PauseCircle,
  FileText,
  MailQuestion,
} from 'lucide-react'
import { cn } from '@/lib/utils'

export type StatusVariant =
  | 'success'
  | 'warning'
  | 'danger'
  | 'neutral'
  | 'info'

interface StatusChipProps {
  status: string
  label?: string
  variant?: StatusVariant
  className?: string
  size?: 'sm' | 'md'
}

export function StatusChip({
  status,
  label,
  variant,
  className,
  size = 'sm',
}: StatusChipProps) {
  const normalized = status.toLowerCase().replace(/[\s-_]+/g, '_')

  // Auto-detect variant and icon if not explicitly provided
  let detectedVariant: StatusVariant = variant || 'neutral'
  let IconComponent = HelpCircle

  switch (normalized) {
    case 'valid':
    case 'approved':
    case 'sent':
    case 'sent_dry_run':
    case 'replied':
    case 'positive_hint':
    case 'won':
    case 'running':
    case 'healthy':
    case 'active':
    case 'completed':
    case 'ok':
      detectedVariant = variant || 'success'
      if (normalized === 'won') IconComponent = Trophy
      else if (normalized.includes('replied')) IconComponent = MessageSquare
      else if (normalized.includes('sent')) IconComponent = Send
      else IconComponent = CheckCircle2
      break

    case 'warning':
    case 'risky':
    case 'soft_bounce':
    case 'paused':
    case 'draft':
    case 'needs_manual_note':
    case 'in_sequence':
    case 'scheduled':
    case 'pending':
    case 'personalized':
      detectedVariant = variant || 'warning'
      if (normalized === 'paused') IconComponent = PauseCircle
      else if (normalized === 'scheduled' || normalized === 'in_sequence') IconComponent = Clock
      else if (normalized === 'needs_manual_note' || normalized === 'personalized') IconComponent = FileText
      else IconComponent = AlertTriangle
      break

    case 'danger':
    case 'critical':
    case 'invalid':
    case 'bad':
    case 'bounced':
    case 'rejected':
    case 'killswitch':
    case 'stopped':
    case 'failed':
      detectedVariant = variant || 'danger'
      if (normalized === 'killswitch' || normalized === 'stopped') IconComponent = ShieldAlert
      else IconComponent = XCircle
      break

    case 'imported':
    case 'new':
    case 'auto_reply':
    case 'other':
    case 'unsubscribed':
    case 'not_interested':
    default:
      detectedVariant = variant || 'neutral'
      if (normalized === 'auto_reply') IconComponent = MailQuestion
      else IconComponent = FileText
      break
  }

  const variantStyles: Record<StatusVariant, string> = {
    success:
      'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800',
    warning:
      'bg-amber-50 text-amber-700 border-amber-200 dark:bg-amber-950/40 dark:text-amber-400 dark:border-amber-800',
    danger:
      'bg-red-50 text-red-700 border-red-200 dark:bg-red-950/40 dark:text-red-400 dark:border-red-800',
    neutral:
      'bg-slate-100 text-slate-700 border-slate-200 dark:bg-slate-800/60 dark:text-slate-300 dark:border-slate-700',
    info:
      'bg-indigo-50 text-indigo-700 border-indigo-200 dark:bg-indigo-950/40 dark:text-indigo-400 dark:border-indigo-800',
  }

  const displayLabel = label || status.replace(/_/g, ' ')

  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 font-medium border rounded-full capitalize select-none tabular-nums',
        size === 'sm' ? 'px-2.5 py-0.5 text-xs' : 'px-3 py-1 text-sm',
        variantStyles[detectedVariant],
        className
      )}
    >
      <IconComponent className={size === 'sm' ? 'w-3.5 h-3.5 shrink-0' : 'w-4 h-4 shrink-0'} aria-hidden="true" />
      <span>{displayLabel}</span>
    </span>
  )
}
