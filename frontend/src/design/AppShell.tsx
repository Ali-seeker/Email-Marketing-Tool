import React, { useEffect, useState } from 'react'
import { NavLink, Link, useNavigate } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  LayoutDashboard,
  Users,
  CheckSquare,
  MessageSquare,
  Send,
  ShieldCheck,
  Settings as SettingsIcon,
  Moon,
  Sun,
  AlertTriangle,
  ShieldAlert,
  ChevronDown,
  Activity,
  Radio,
  Flame,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { useTheme } from './ThemeProvider'
import { useToast } from './Toast'
import { api } from '@/api/client'

export function AppShell({ children }: { children: React.ReactNode }) {
  const { theme, toggleTheme } = useTheme()
  const { toast } = useToast()
  const queryClient = useQueryClient()
  const navigate = useNavigate()

  // 1. Fetch Safety Status
  const { data: safetyStatus } = useQuery({
    queryKey: ['safetyStatus'],
    queryFn: () => api.safety.status(),
    refetchInterval: 10000,
  })

  // 2. Fetch Settings (for DRY-RUN vs LIVE display)
  const { data: settingsData } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.settings.get(),
  })

  // 3. Fetch Campaigns for TopBar Switcher
  const { data: campaignsData } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api.campaigns.list({ page_size: 20 }),
  })

  // 4. Fetch Badge Counts
  const { data: pendingApprovalData } = useQuery({
    queryKey: ['pendingApprovalCount'],
    queryFn: () => api.approval.listPending({ page_size: 1 }),
    refetchInterval: 15000,
  })

  const { data: unhandledRepliesData } = useQuery({
    queryKey: ['unhandledRepliesCount'],
    queryFn: () => api.replies.list({ page_size: 1, unhandled_only: true }),
    refetchInterval: 15000,
  })

  // SSE Live Event Listener
  useEffect(() => {
    let eventSource: EventSource | null = null
    try {
      const url = api.events.getStreamUrl()
      eventSource = new EventSource(url)

      eventSource.addEventListener('reply', (e) => {
        try {
          const payload = JSON.parse(e.data)
          toast({
            title: payload.is_positive_hint ? '🎯 Positive Reply Received!' : 'New Reply Received',
            message: `${payload.from_email || 'Lead'}: ${payload.subject || ''}`,
            type: payload.is_positive_hint ? 'success' : 'info',
            duration: 6000,
          })
          queryClient.invalidateQueries({ queryKey: ['replies'] })
          queryClient.invalidateQueries({ queryKey: ['unhandledRepliesCount'] })
          queryClient.invalidateQueries({ queryKey: ['overviewStats'] })
        } catch {
          // ignore parsing error
        }
      })

      eventSource.addEventListener('killswitch', (e) => {
        try {
          const payload = JSON.parse(e.data)
          toast({
            title: '⚠️ EMERGENCY KILLSWITCH STATUS CHANGED',
            message: payload.reason || payload.state || 'Outreach state altered',
            type: 'error',
            duration: 10000,
          })
          queryClient.invalidateQueries({ queryKey: ['safetyStatus'] })
        } catch {
          // ignore parsing error
        }
      })
    } catch {
      // EventSource fallback
    }

    return () => {
      eventSource?.close()
    }
  }, [queryClient, toast])

  const pendingCount = pendingApprovalData?.total || 0
  const repliesCount = unhandledRepliesData?.total || 0
  const isKillswitchActive = safetyStatus?.killswitch_active ?? false
  const healthSeverity = safetyStatus?.severity || 'OK' // OK, WARNING, CRITICAL
  const isLive = Boolean(settingsData?.LIVE_MODE)

  const navItems = [
    { name: 'Overview', to: '/', icon: LayoutDashboard },
    { name: 'Leads', to: '/leads', icon: Users },
    {
      name: 'Approval Queue',
      to: '/approval',
      icon: CheckSquare,
      badge: pendingCount > 0 ? pendingCount : undefined,
      badgeVariant: 'warning',
    },
    {
      name: 'Replies',
      to: '/replies',
      icon: MessageSquare,
      badge: repliesCount > 0 ? repliesCount : undefined,
      badgeVariant: 'info',
    },
    { name: 'Campaigns', to: '/campaigns', icon: Send },
    {
      name: 'Safety',
      to: '/safety',
      icon: ShieldCheck,
      badge: isKillswitchActive ? 'HALTED' : undefined,
      badgeVariant: 'danger',
    },
    { name: 'Settings', to: '/settings', icon: SettingsIcon },
  ]

  const campaigns = campaignsData?.items || []
  const [selectedCampaignId, setSelectedCampaignId] = useState<number | 'all'>('all')

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col antialiased">
      {/* 1. Global Full-Width Health Banner (Warning or Critical) */}
      {(healthSeverity === 'WARNING' || healthSeverity === 'CRITICAL' || isKillswitchActive) && (
        <div
          role="alert"
          className={cn(
            'w-full px-4 py-2 flex items-center justify-between text-xs font-medium z-30 transition-colors',
            isKillswitchActive || healthSeverity === 'CRITICAL'
              ? 'bg-red-600 text-white'
              : 'bg-amber-500 text-slate-950 font-semibold'
          )}
        >
          <div className="flex items-center gap-2 max-w-4xl truncate">
            {isKillswitchActive ? (
              <ShieldAlert className="w-4 h-4 shrink-0 animate-pulse" aria-hidden="true" />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
            )}
            <span className="truncate">
              <strong>
                {isKillswitchActive
                  ? 'EMERGENCY KILL-SWITCH ENGAGED:'
                  : healthSeverity === 'CRITICAL'
                  ? 'CRITICAL SAFETY ALERT:'
                  : 'SAFETY HEALTH WARNING:'}
              </strong>{' '}
              {safetyStatus?.reasons?.[0] ||
                (isKillswitchActive
                  ? 'Outreach paused by safety monitor'
                  : 'Elevated bounce or complaint rate')}
            </span>
          </div>
          <Link
            to="/safety"
            className={cn(
              'underline underline-offset-2 shrink-0 hover:opacity-85 text-xs font-bold ml-4'
            )}
          >
            Resolve in Safety →
          </Link>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
        {/* 2. Left Sidebar (240px desktop, collapses to icons under 1024px) */}
        <aside
          aria-label="Sidebar navigation"
          className="w-16 lg:w-60 bg-card border-r border-border shrink-0 flex flex-col justify-between transition-all duration-200 z-20"
        >
          <div>
            {/* Brand Logo & Title */}
            <div className="h-14 flex items-center px-4 lg:px-5 border-b border-border gap-3">
              <div className="w-8 h-8 rounded-lg bg-primary text-primary-foreground flex items-center justify-center font-bold text-sm tracking-wider shrink-0 shadow-xs">
                CR
              </div>
              <div className="hidden lg:flex flex-col min-w-0">
                <span className="font-bold text-sm tracking-tight text-foreground truncate">
                  ColdReach
                </span>
                <span className="text-[10px] text-muted-foreground uppercase tracking-widest font-semibold">
                  Operator Desk
                </span>
              </div>
            </div>

            {/* Navigation Links */}
            <nav className="p-2 lg:p-3 space-y-1">
              {navItems.map((item) => {
                const Icon = item.icon
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === '/'}
                    className={({ isActive }) =>
                      cn(
                        'flex items-center gap-3 px-3 py-2 rounded-md text-xs font-medium transition-colors select-none group',
                        isActive
                          ? 'bg-primary/10 text-primary font-semibold'
                          : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                      )
                    }
                  >
                    <Icon className="w-4 h-4 shrink-0 transition-transform group-hover:scale-105" aria-hidden="true" />
                    <span className="hidden lg:inline truncate flex-1">{item.name}</span>
                    {item.badge !== undefined && (
                      <span
                        className={cn(
                          'hidden lg:inline-flex px-1.5 py-0.2 rounded-full text-[10px] font-bold tabular-nums',
                          item.badgeVariant === 'danger'
                            ? 'bg-red-100 text-red-700 dark:bg-red-950 dark:text-red-400'
                            : item.badgeVariant === 'warning'
                            ? 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300'
                            : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300'
                        )}
                      >
                        {item.badge}
                      </span>
                    )}
                  </NavLink>
                )
              })}
            </nav>
          </div>

          {/* Sidebar Footer info */}
          <div className="p-3 border-t border-border hidden lg:block text-[11px] text-muted-foreground">
            <div className="flex items-center justify-between">
              <span>Safety Guard</span>
              <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                <Activity className="w-3 h-3" /> Active
              </span>
            </div>
          </div>
        </aside>

        {/* Main Content Area */}
        <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {/* 3. Top Bar */}
          <header className="h-14 bg-card border-b border-border flex items-center justify-between px-4 lg:px-6 shrink-0 z-10 gap-4">
            {/* Left: Campaign Switcher */}
            <div className="flex items-center gap-3 min-w-0">
              <label htmlFor="campaign-switcher" className="sr-only">
                Switch Campaign
              </label>
              <div className="relative">
                <select
                  id="campaign-switcher"
                  value={selectedCampaignId}
                  onChange={(e) => {
                    const val = e.target.value === 'all' ? 'all' : Number(e.target.value)
                    setSelectedCampaignId(val)
                  }}
                  className="h-8 pl-3 pr-8 text-xs font-medium bg-muted/60 hover:bg-muted border border-border rounded-md appearance-none focus:outline-hidden focus:ring-1 focus:ring-primary cursor-pointer truncate max-w-[200px] lg:max-w-[280px]"
                >
                  <option value="all">All Campaigns</option>
                  {campaigns.map((camp) => (
                    <option key={camp.id} value={camp.id}>
                      {camp.name} ({camp.leads_count} leads)
                    </option>
                  ))}
                </select>
                <ChevronDown className="w-3.5 h-3.5 text-muted-foreground absolute right-2.5 top-2.5 pointer-events-none" />
              </div>
            </div>

            {/* Right: Mode Badge, Killswitch Pill, Theme Toggle */}
            <div className="flex items-center gap-2.5 shrink-0">
              {/* DRY-RUN or LIVE Badge (read from server, cannot change in UI) */}
              <div
                title={
                  isLive
                    ? 'LIVE MODE: Real SMTP emails leave this host machine'
                    : 'DRY-RUN MODE: Outbound emails are stored safely in local outbox only'
                }
                className={cn(
                  'px-2.5 py-1 rounded-full text-xs font-bold tracking-wide uppercase flex items-center gap-1.5 border select-none',
                  isLive
                    ? 'bg-rose-100 text-rose-800 border-rose-300 dark:bg-rose-950/60 dark:text-rose-300 dark:border-rose-800'
                    : 'bg-emerald-100 text-emerald-800 border-emerald-300 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800'
                )}
              >
                {isLive ? (
                  <>
                    <Flame className="w-3 h-3 text-rose-600 animate-pulse" aria-hidden="true" />
                    LIVE
                  </>
                ) : (
                  <>
                    <Radio className="w-3 h-3 text-emerald-600" aria-hidden="true" />
                    DRY-RUN
                  </>
                )}
              </div>

              {/* Killswitch Status Pill */}
              <button
                type="button"
                onClick={() => navigate('/safety')}
                title={
                  isKillswitchActive
                    ? 'Emergency killswitch active! Click to review in Safety.'
                    : 'Killswitch armed and normal dispatch active.'
                }
                className={cn(
                  'px-2.5 py-1 rounded-full text-xs font-semibold flex items-center gap-1.5 border transition-all cursor-pointer',
                  isKillswitchActive
                    ? 'bg-red-100 text-red-800 border-red-300 dark:bg-red-950 dark:text-red-300 dark:border-red-800'
                    : 'bg-emerald-50 text-emerald-700 border-emerald-200 dark:bg-emerald-950/40 dark:text-emerald-400 dark:border-emerald-800'
                )}
              >
                <span
                  className={cn(
                    'w-2 h-2 rounded-full',
                    isKillswitchActive ? 'bg-red-600 animate-ping' : 'bg-emerald-500'
                  )}
                  aria-hidden="true"
                />
                <span className="hidden sm:inline">
                  {isKillswitchActive ? 'STOPPED' : 'Running'}
                </span>
              </button>

              {/* Theme Toggle */}
              <button
                type="button"
                onClick={toggleTheme}
                aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
                className="w-8 h-8 rounded-md border border-border flex items-center justify-center hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              >
                {theme === 'dark' ? (
                  <Sun className="w-4 h-4 text-amber-400" aria-hidden="true" />
                ) : (
                  <Moon className="w-4 h-4 text-slate-700" aria-hidden="true" />
                )}
              </button>
            </div>
          </header>

          {/* Main Scrollable View */}
          <main className="flex-1 overflow-y-auto p-4 lg:p-6 bg-background">
            <div className="max-w-7xl mx-auto w-full space-y-6">
              {children}
            </div>
          </main>
        </div>
      </div>
    </div>
  )
}
