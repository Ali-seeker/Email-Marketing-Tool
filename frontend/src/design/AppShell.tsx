import React, { useEffect, useState, useRef } from 'react'
import { NavLink, Link, useNavigate, useSearchParams, useLocation } from 'react-router-dom'
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
  Bell,
  Mail,
  CheckCheck,
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
  const [searchParams, setSearchParams] = useSearchParams()
  const location = useLocation()

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



  // Notifications dropdown replies
  const { data: recentRepliesData } = useQuery({
    queryKey: ['recentRepliesNotifications'],
    queryFn: () => api.replies.list({ page_size: 8 }),
    refetchInterval: 3000,
  })

  // Track read reply IDs in localStorage for instant notification clearing
  const [readReplyIds, setReadReplyIds] = useState<number[]>(() => {
    try {
      const stored = localStorage.getItem('coldreach_read_reply_ids')
      return stored ? JSON.parse(stored) : []
    } catch {
      return []
    }
  })

  const markReplyAsRead = (id: number) => {
    setReadReplyIds((prev) => {
      if (prev.includes(id)) return prev
      const updated = [...prev, id]
      try {
        localStorage.setItem('coldreach_read_reply_ids', JSON.stringify(updated))
      } catch {}
      return updated
    })
  }

  const markAllRepliesAsRead = () => {
    const allIds = recentRepliesData?.items?.map((r) => r.id) || []
    setReadReplyIds((prev) => {
      const combined = Array.from(new Set([...prev, ...allIds]))
      try {
        localStorage.setItem('coldreach_read_reply_ids', JSON.stringify(combined))
      } catch {}
      return combined
    })
    toast({
      title: 'Notifications Cleared',
      message: 'Tamam reply notifications read mark ho gaye hain.',
      type: 'info',
    })
  }

  // When user is viewing /replies page, automatically mark currently loaded replies as read
  useEffect(() => {
    if (location.pathname === '/replies' && recentRepliesData?.items?.length) {
      const currentIds = recentRepliesData.items.map((r) => r.id)
      setReadReplyIds((prev) => {
        const hasUnread = currentIds.some((id) => !prev.includes(id))
        if (!hasUnread) return prev
        const combined = Array.from(new Set([...prev, ...currentIds]))
        try {
          localStorage.setItem('coldreach_read_reply_ids', JSON.stringify(combined))
        } catch {}
        return combined
      })
    }
  }, [location.pathname, recentRepliesData])

  const [isNotificationsOpen, setIsNotificationsOpen] = useState(false)
  const notificationsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (notificationsRef.current && !notificationsRef.current.contains(event.target as Node)) {
        setIsNotificationsOpen(false)
      }
    }
    if (isNotificationsOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isNotificationsOpen])

  // Live Mode Toggle State
  const [showLiveConfirmModal, setShowLiveConfirmModal] = useState(false)
  const [isTogglingMode, setIsTogglingMode] = useState(false)

  const handleToggleMode = async (targetLive: boolean) => {
    try {
      setIsTogglingMode(true)
      const res = await api.settings.toggleLiveMode(targetLive)
      toast({
        title: targetLive ? '🔥 LIVE Mode Activated' : '🛡️ DRY-RUN Mode Activated',
        message: res.message,
        type: targetLive ? 'warning' : 'success',
      })
      queryClient.invalidateQueries({ queryKey: ['settings'] })
    } catch (err: any) {
      toast({
        title: 'Mode Switch Failed',
        message: err.message || 'Could not change operating mode',
        type: 'error',
      })
    } finally {
      setIsTogglingMode(false)
      setShowLiveConfirmModal(false)
    }
  }

  // Hunter Quota Alert Dismissal State
  const [isDismissingHunterAlert, setIsDismissingHunterAlert] = useState(false)
  const [localHunterDismissed, setLocalHunterDismissed] = useState(false)

  const showHunterQuotaModal = Boolean(
    safetyStatus?.hunter_quota_exceeded &&
    !safetyStatus?.hunter_quota_dismissed &&
    !localHunterDismissed
  )

  const handleDismissHunterAlert = async () => {
    try {
      setIsDismissingHunterAlert(true)
      await api.safety.dismissHunterAlert()
      setLocalHunterDismissed(true)
      await queryClient.invalidateQueries({ queryKey: ['safetyStatus'] })
      toast({
        title: 'Hunter.io Alert Dismissed',
        message: 'System local fallback mode me verification jari rakhay ga.',
        type: 'info',
      })
    } catch (err: any) {
      toast({
        title: 'Action Failed',
        message: err.message || 'Could not update alert status',
        type: 'error',
      })
    } finally {
      setIsDismissingHunterAlert(false)
    }
  }

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
  const unreadReplies = (recentRepliesData?.items || []).filter(
    (reply) => !readReplyIds.includes(reply.id)
  )
  const repliesCount = unreadReplies.length
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
    { name: 'Mailboxes', to: '/mailboxes', icon: Mail },
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
  const campaignParam = searchParams.get('campaign')
  const selectedCampaignId = campaignParam ? Number(campaignParam) : 'all'

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
                    const val = e.target.value
                    const nextParams = new URLSearchParams(searchParams)
                    if (val === 'all') {
                      nextParams.delete('campaign')
                    } else {
                      nextParams.set('campaign', val)
                    }
                    if (location.pathname !== '/leads' && location.pathname !== '/campaigns') {
                      navigate(`/leads?${nextParams.toString()}`)
                    } else {
                      setSearchParams(nextParams)
                    }
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
              {/* DRY-RUN vs LIVE Mode Toggle Button */}
              <button
                type="button"
                onClick={() => {
                  if (isLive) {
                    handleToggleMode(false)
                  } else {
                    setShowLiveConfirmModal(true)
                  }
                }}
                disabled={isTogglingMode}
                title={
                  isLive
                    ? 'Click to switch safely to DRY-RUN mode'
                    : 'Click to switch to LIVE sending mode'
                }
                className={cn(
                  'px-2.5 py-1 rounded-full text-xs font-bold tracking-wide uppercase flex items-center gap-1.5 border transition-all cursor-pointer shadow-xs hover:opacity-90 active:scale-95 select-none',
                  isLive
                    ? 'bg-rose-100 text-rose-800 border-rose-300 hover:bg-rose-200 dark:bg-rose-950/60 dark:text-rose-300 dark:border-rose-800 dark:hover:bg-rose-900/60'
                    : 'bg-emerald-100 text-emerald-800 border-emerald-300 hover:bg-emerald-200 dark:bg-emerald-950/60 dark:text-emerald-300 dark:border-emerald-800 dark:hover:bg-emerald-900/60'
                )}
              >
                {isLive ? (
                  <>
                    <Flame className="w-3 h-3 text-rose-600 animate-pulse" aria-hidden="true" />
                    <span>LIVE</span>
                  </>
                ) : (
                  <>
                    <Radio className="w-3 h-3 text-emerald-600" aria-hidden="true" />
                    <span>DRY-RUN</span>
                  </>
                )}
              </button>

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

              {/* Reply Notifications Bell */}
              <div className="relative" ref={notificationsRef}>
                <button
                  type="button"
                  onClick={() => setIsNotificationsOpen((prev) => !prev)}
                  title="Lead replies notifications"
                  className={cn(
                    'relative w-8 h-8 rounded-md border border-border flex items-center justify-center transition-colors cursor-pointer',
                    isNotificationsOpen
                      ? 'bg-muted text-foreground'
                      : 'text-muted-foreground hover:text-foreground hover:bg-muted'
                  )}
                  aria-label="View reply notifications"
                  aria-expanded={isNotificationsOpen}
                >
                  <Bell className="w-4 h-4" aria-hidden="true" />
                  {repliesCount > 0 && (
                    <span className="absolute -top-1 -right-1 min-w-[17px] h-[17px] px-1 bg-rose-500 text-white text-[10px] font-bold rounded-full flex items-center justify-center shadow-xs animate-pulse">
                      {repliesCount > 99 ? '99+' : repliesCount}
                    </span>
                  )}
                </button>

                {isNotificationsOpen && (
                  <div className="absolute right-0 mt-2 w-80 sm:w-96 bg-card border border-border rounded-xl shadow-xl z-50 overflow-hidden animate-in fade-in zoom-in-95 duration-100">
                    <div className="p-3 bg-muted/50 border-b border-border flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <MessageSquare className="w-4 h-4 text-primary" />
                        <span className="text-xs font-bold text-foreground">Lead Replies</span>
                        {repliesCount > 0 ? (
                          <span className="px-1.5 py-0.2 text-[10px] font-semibold bg-rose-500/15 text-rose-600 dark:text-rose-400 rounded-full">
                            {repliesCount} new
                          </span>
                        ) : (
                          <span className="px-1.5 py-0.2 text-[10px] font-semibold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 rounded-full">
                            All read
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2">
                        {repliesCount > 0 && (
                          <button
                            type="button"
                            onClick={markAllRepliesAsRead}
                            className="text-[11px] font-semibold text-primary hover:underline flex items-center gap-1 cursor-pointer"
                            title="Mark all notifications as read"
                          >
                            <CheckCheck className="w-3.5 h-3.5 text-emerald-500" />
                            Clear all
                          </button>
                        )}
                        <Link
                          to="/replies"
                          onClick={() => {
                            markAllRepliesAsRead()
                            setIsNotificationsOpen(false)
                          }}
                          className="text-[11px] font-medium text-muted-foreground hover:text-foreground flex items-center gap-1"
                        >
                          All Replies →
                        </Link>
                      </div>
                    </div>

                    <div className="max-h-[380px] overflow-y-auto divide-y divide-border/60">
                      {recentRepliesData?.items && recentRepliesData.items.length > 0 ? (
                        recentRepliesData.items.map((reply) => {
                          const isPositive = reply.is_positive_hint || reply.classification === 'positive'
                          const isUnread = !readReplyIds.includes(reply.id)
                          return (
                            <div
                              key={reply.id}
                              onClick={() => {
                                markReplyAsRead(reply.id)
                                setIsNotificationsOpen(false)
                                navigate(`/replies?reply_id=${reply.id}${reply.to_email ? `&to_email=${encodeURIComponent(reply.to_email)}` : ''}`)
                              }}
                              className={cn(
                                'p-3 hover:bg-muted/60 transition-colors cursor-pointer group',
                                isUnread ? 'bg-primary/5 dark:bg-primary/10' : 'opacity-85'
                              )}
                            >
                              <div className="flex items-start justify-between gap-2">
                                <div className="min-w-0 flex-1">
                                  <div className="flex items-center gap-1.5">
                                    {isUnread && (
                                      <span className="w-1.5 h-1.5 rounded-full bg-rose-500 shrink-0" title="Unread" />
                                    )}
                                    <span className="text-xs font-semibold text-foreground truncate group-hover:text-primary transition-colors">
                                      {reply.from_email}
                                    </span>
                                    {isPositive && (
                                      <span className="px-1.5 py-0.2 text-[9px] font-bold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 rounded-sm">
                                        Positive
                                      </span>
                                    )}
                                  </div>
                                  <p className="text-[11px] font-medium text-muted-foreground truncate mt-0.5">
                                    {reply.subject || '(No subject)'}
                                  </p>
                                  <p className="text-[11px] text-muted-foreground/80 line-clamp-2 mt-0.5 leading-snug">
                                    {reply.snippet || reply.body}
                                  </p>
                                </div>
                                <span className="text-[10px] text-muted-foreground shrink-0 tabular-nums">
                                  {new Date(reply.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                </span>
                              </div>
                              {reply.to_email && (
                                <div className="mt-1.5 flex items-center gap-1 text-[10px] text-muted-foreground">
                                  <Mail className="w-2.5 h-2.5 text-primary" />
                                  <span className="truncate">to: {reply.to_email}</span>
                                </div>
                              )}
                            </div>
                          )
                        })
                      ) : (
                        <div className="p-8 text-center">
                          <MessageSquare className="w-8 h-8 text-muted-foreground/40 mx-auto mb-2" />
                          <p className="text-xs font-medium text-foreground">No replies yet</p>
                          <p className="text-[11px] text-muted-foreground mt-0.5">
                            When prospects reply to your cold emails, notifications will appear here.
                          </p>
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>

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

      {/* Confirm Switch to LIVE Mode Modal */}
      {showLiveConfirmModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-xs animate-in fade-in"
        >
          <div className="bg-card border border-border w-full max-w-md rounded-xl shadow-2xl p-5 space-y-4 animate-in zoom-in-95">
            <div className="flex items-center gap-3 text-rose-600">
              <div className="w-10 h-10 rounded-full bg-rose-500/10 flex items-center justify-center shrink-0">
                <Flame className="w-5 h-5 animate-pulse" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-foreground">Switch to LIVE Sending Mode?</h3>
                <p className="text-xs text-muted-foreground">Outbound emails will leave this machine</p>
              </div>
            </div>

            <div className="p-3 bg-rose-500/10 border border-rose-500/20 rounded-lg text-xs text-rose-800 dark:text-rose-300 space-y-1.5">
              <p className="font-semibold flex items-center gap-1.5">
                <AlertTriangle className="w-4 h-4 shrink-0" /> Real Outreach Warning
              </p>
              <p>
                In <strong>LIVE</strong> mode, emails will be delivered directly to prospective clients via your connected SMTP mailboxes. Ensure sequences and recipients are verified before proceeding.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <button
                type="button"
                onClick={() => setShowLiveConfirmModal(false)}
                className="px-3 py-1.5 text-xs font-medium border border-border hover:bg-muted text-foreground rounded-md transition-colors cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={isTogglingMode}
                onClick={() => handleToggleMode(true)}
                className="px-4 py-1.5 text-xs font-semibold bg-rose-600 hover:bg-rose-700 text-white rounded-md flex items-center gap-1.5 shadow-xs transition-colors cursor-pointer"
              >
                {isTogglingMode ? (
                  'Switching...'
                ) : (
                  <>
                    <Flame className="w-3.5 h-3.5" /> Confirm & Activate LIVE
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Persistent Hunter.io Quota Exceeded Modal */}
      {showHunterQuotaModal && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-background/80 backdrop-blur-xs animate-in fade-in"
        >
          <div className="bg-card border-2 border-amber-500/40 w-full max-w-lg rounded-xl shadow-2xl p-6 space-y-5 animate-in zoom-in-95">
            <div className="flex items-start gap-4">
              <div className="w-12 h-12 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-500 flex items-center justify-center shrink-0 shadow-xs">
                <AlertTriangle className="w-6 h-6 animate-pulse" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <h3 className="text-base font-bold text-foreground">Hunter.io API Limit Reached</h3>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-500/15 text-amber-600 dark:text-amber-400 border border-amber-500/30 uppercase tracking-wider">
                    Limit Exhausted
                  </span>
                </div>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Monthly verification credit cap has been reached
                </p>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <div className="p-3 bg-muted/60 border border-border rounded-lg space-y-1">
                <span className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Credits Status</span>
                <p className="font-bold text-foreground text-sm">
                  {safetyStatus?.hunter_used_credits ?? safetyStatus?.hunter_monthly_quota ?? 25} / {safetyStatus?.hunter_monthly_quota ?? 25} Used
                </p>
                <span className="text-[11px] text-amber-600 dark:text-amber-400">100% Consumed</span>
              </div>
              <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-lg space-y-1">
                <span className="text-[10px] font-semibold text-emerald-700 dark:text-emerald-400 uppercase tracking-wider">System State</span>
                <p className="font-bold text-emerald-600 dark:text-emerald-400 text-sm flex items-center gap-1.5">
                  <Activity className="w-4 h-4" /> Local Fallback Active
                </p>
                <span className="text-[11px] text-emerald-600/80 dark:text-emerald-400/80">Continuous Validation</span>
              </div>
            </div>

            <div className="p-3.5 bg-muted/40 border border-border rounded-lg text-xs text-muted-foreground space-y-2 leading-relaxed">
              <p className="text-foreground font-medium flex items-center gap-1.5">
                <ShieldCheck className="w-4 h-4 text-emerald-500 shrink-0" />
                Automatic Fallback Mode Active
              </p>
              <p>
                Hunter.io ki monthly limit poori hone par verification ruke gi nahi. System ne automatically local engine switch kar diya hai:
              </p>
              <ul className="list-disc list-inside space-y-1 pl-1 text-[11px]">
                <li>RFC 5322 Syntax & Common Typo corrections (e.g. <code className="text-foreground font-mono">@gail.com</code>)</li>
                <li>Live MX & Null MX (RFC 7505) DNS records validation</li>
                <li>Disposable & Temporary email address blocking</li>
                <li>Role-based accounts & company website alignment</li>
              </ul>
              <p className="text-[11px] text-amber-600 dark:text-amber-400 font-medium pt-1 border-t border-border">
                ⚠️ Yeh notice screen par tab tak rahay ga jab tak aap khud isay dismiss na karein.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-border">
              <button
                type="button"
                disabled={isDismissingHunterAlert}
                onClick={handleDismissHunterAlert}
                className="w-full sm:w-auto px-5 py-2 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-lg shadow-sm transition-all cursor-pointer flex items-center justify-center gap-2"
              >
                {isDismissingHunterAlert ? (
                  'Dismissing...'
                ) : (
                  <>
                    <CheckSquare className="w-4 h-4" /> Samajh Gaya, Continue with Fallback
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
