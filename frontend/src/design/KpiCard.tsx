import React from 'react'
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react'
import { cn } from '@/lib/utils'

interface KpiCardProps {
  label: string
  value: string | number
  delta?: {
    value: string
    isPositive?: boolean
    isNeutral?: boolean
  }
  sparklineData?: number[]
  progressBar?: {
    current: number
    max: number
  }
  statusVariant?: 'normal' | 'warning' | 'danger'
  tooltip?: string
  className?: string
  icon?: React.ReactNode
}

export function KpiCard({
  label,
  value,
  delta,
  sparklineData,
  progressBar,
  statusVariant = 'normal',
  tooltip,
  className,
  icon,
}: KpiCardProps) {
  // Render mini SVG sparkline
  const renderSparkline = () => {
    if (!sparklineData || sparklineData.length < 2) return null
    const min = Math.min(...sparklineData)
    const max = Math.max(...sparklineData)
    const range = max - min === 0 ? 1 : max - min
    const width = 64
    const height = 24

    const points = sparklineData
      .map((val, idx) => {
        const x = (idx / (sparklineData.length - 1)) * width
        const y = height - ((val - min) / range) * (height - 4) - 2
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')

    const strokeColor =
      statusVariant === 'danger'
        ? 'hsl(var(--danger))'
        : statusVariant === 'warning'
        ? 'hsl(var(--warning))'
        : 'hsl(var(--primary))'

    return (
      <svg
        className="w-16 h-6 overflow-visible shrink-0"
        viewBox={`0 0 ${width} ${height}`}
        aria-hidden="true"
      >
        <polyline
          fill="none"
          stroke={strokeColor}
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={points}
        />
      </svg>
    )
  }

  const borderVariantClass =
    statusVariant === 'danger'
      ? 'border-red-300 dark:border-red-900/60'
      : statusVariant === 'warning'
      ? 'border-amber-300 dark:border-amber-900/60'
      : 'border-border'

  return (
    <div
      title={tooltip}
      className={cn(
        'p-4 bg-card rounded-lg border shadow-xs flex flex-col justify-between transition-all duration-150',
        borderVariantClass,
        className
      )}
    >
      <div className="flex items-center justify-between text-muted-foreground text-xs font-medium uppercase tracking-wider mb-2">
        <span className="truncate">{label}</span>
        {icon && <span className="text-muted-foreground/80">{icon}</span>}
      </div>

      <div className="flex items-baseline justify-between gap-2">
        <div className="text-2xl font-bold text-foreground tabular-nums tracking-tight">
          {value}
        </div>
        {renderSparkline()}
      </div>

      {progressBar && (
        <div className="mt-3">
          <div
            role="progressbar"
            aria-valuenow={Math.round((progressBar.current / (progressBar.max || 1)) * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`${label} progress`}
            className="w-full bg-secondary rounded-full h-1.5 overflow-hidden"
          >
            <div
              className={cn(
                'h-full transition-all duration-300 rounded-full',
                statusVariant === 'danger'
                  ? 'bg-red-500'
                  : statusVariant === 'warning'
                  ? 'bg-amber-500'
                  : 'bg-primary'
              )}
              style={{
                width: `${Math.min(100, Math.max(0, (progressBar.current / (progressBar.max || 1)) * 100))}%`,
              }}
            />
          </div>
          <div className="flex justify-between text-[11px] text-muted-foreground mt-1 tabular-nums">
            <span>{progressBar.current} sent</span>
            <span>{progressBar.max} daily cap</span>
          </div>
        </div>
      )}

      {delta && !progressBar && (
        <div className="flex items-center gap-1.5 mt-2 text-xs tabular-nums">
          {delta.isNeutral ? (
            <Minus className="w-3.5 h-3.5 text-muted-foreground" aria-hidden="true" />
          ) : delta.isPositive ? (
            <ArrowUpRight className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" aria-hidden="true" />
          ) : (
            <ArrowDownRight className="w-3.5 h-3.5 text-red-600 dark:text-red-400" aria-hidden="true" />
          )}
          <span
            className={cn(
              'font-medium',
              delta.isNeutral
                ? 'text-muted-foreground'
                : delta.isPositive
                ? 'text-emerald-600 dark:text-emerald-400'
                : 'text-red-600 dark:text-red-400'
            )}
          >
            {delta.value}
          </span>
          <span className="text-muted-foreground">vs prev period</span>
        </div>
      )}
    </div>
  )
}
