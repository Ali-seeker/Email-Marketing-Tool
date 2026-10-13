import React, { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import {
  Upload,
  Search,
  AlertCircle,
  Pause,
  Play,
  Trophy,
  MessageSquare,
  Ban,
  FastForward,
  Download,
  Send,
  Clock,
  Sparkles,
  ShieldCheck,
  Loader2,
  ChevronDown,
  ChevronUp,
} from 'lucide-react'
import { api } from '@/api/client'
import type {
  Lead,
  ImportReport,
  RejectItem,
  LeadTimelineEmail,
  LeadTimelineReply,
  LeadTimelineEvent,
} from '@/api/types'
import { DataTable } from '@/design/DataTable'
import { StatusChip } from '@/design/StatusChip'
import { SideDrawer } from '@/design/Overlays'
import { Skeleton } from '@/design/Feedback'
import { useToast } from '@/design/Toast'

export function LeadsPage() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [searchParams, setSearchParams] = useSearchParams()
  const campaignIdParam = searchParams.get('campaign') ? Number(searchParams.get('campaign')) : undefined

  // State: Filters, search, pagination, drawer, modal
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [riskFilter, setRiskFilter] = useState('')
  const [countryFilter, setCountryFilter] = useState('')
  const [page, setPage] = useState(1)
  const [pageSize] = useState(50)

  // Drawer & Selection
  const [selectedLeadId, setSelectedLeadId] = useState<number | null>(null)
  const [selectedLeads, setSelectedLeads] = useState<Lead[]>([])
  const [expandedStepIds, setExpandedStepIds] = useState<Record<number, boolean>>({})

  // Fetch campaigns for top context & CSV defaults
  const { data: campaignsData } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api.campaigns.list({ page_size: 50 }),
  })
  const currentCampaign = campaignsData?.items?.find((c) => c.id === campaignIdParam)

  // CSV Upload Dialog State
  const [isUploadOpen, setIsUploadOpen] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [uploadCampaignMode, setUploadCampaignMode] = useState<'existing' | 'new'>('existing')
  const [uploadCampaignId, setUploadCampaignId] = useState<number | null>(campaignIdParam || null)
  const [uploadCampaign, setUploadCampaign] = useState(currentCampaign?.name || '')
  const [uploadReport, setUploadReport] = useState<ImportReport | null>(null)

  // Auto-open upload dialog if ?openUpload=1 is passed from Campaigns page
  useEffect(() => {
    if (searchParams.get('openUpload') === '1') {
      setIsUploadOpen(true)
      if (campaignIdParam) {
        setUploadCampaignId(campaignIdParam)
        setUploadCampaignMode('existing')
        const matched = campaignsData?.items?.find((c) => c.id === campaignIdParam)
        if (matched) setUploadCampaign(matched.name)
      }
      searchParams.delete('openUpload')
      setSearchParams(searchParams, { replace: true })
    }
  }, [searchParams, campaignIdParam, campaignsData])

  // 1. Fetch Leads Table Data (Filtered by campaignIdParam if set)
  const { data: leadsData, isLoading } = useQuery({
    queryKey: ['leads', page, pageSize, search, statusFilter, riskFilter, countryFilter, campaignIdParam],
    queryFn: () =>
      api.leads.list({
        page,
        page_size: pageSize,
        search: search || undefined,
        status: statusFilter || undefined,
        risk_level: riskFilter || undefined,
        country: countryFilter || undefined,
        campaign_id: campaignIdParam,
      }),
  })

  // 2. Fetch Selected Lead Detail & Timeline
  const { data: leadTimeline, isLoading: isTimelineLoading } = useQuery({
    queryKey: ['leadTimeline', selectedLeadId],
    queryFn: () => api.leads.get(selectedLeadId!),
    enabled: selectedLeadId !== null,
  })

  // Mutations for Lead Actions
  const pauseMutation = useMutation({
    mutationFn: (id: number) => api.leads.pause(id),
    onSuccess: () => {
      toast({ title: 'Lead Paused', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  const resumeMutation = useMutation({
    mutationFn: (id: number) => api.leads.resume(id),
    onSuccess: () => {
      toast({ title: 'Lead Resumed', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  const markRepliedMutation = useMutation({
    mutationFn: (id: number) => api.leads.markReplied(id),
    onSuccess: () => {
      toast({ title: 'Marked as Replied', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  const markWonMutation = useMutation({
    mutationFn: (id: number) => api.leads.markWon(id),
    onSuccess: () => {
      toast({ title: '🎉 Deal Won!', message: 'Lead converted successfully', type: 'success' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  const notInterestedMutation = useMutation({
    mutationFn: (id: number) => api.leads.markNotInterested(id),
    onSuccess: () => {
      toast({ title: 'Lead Suppressed', message: 'Marked not interested and added to suppression', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  const skipStepMutation = useMutation({
    mutationFn: (id: number) => api.leads.skipStep(id),
    onSuccess: () => {
      toast({ title: 'Step Skipped', type: 'info' })
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['leadTimeline', selectedLeadId] })
    },
  })

  // CSV Upload Mutation
  const uploadMutation = useMutation({
    mutationFn: ({ file, campaign, campaignId }: { file: File; campaign?: string; campaignId?: number }) =>
      api.leads.uploadCsv(file, campaign, campaignId),
    onSuccess: (report) => {
      setUploadReport(report)
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      toast({
        title: 'CSV Import Completed',
        message: `Imported ${report.imported} leads, ${report.duplicates} duplicates, ${report.rejected} rejected`,
        type: 'success',
      })
    },
    onError: (err: any) => {
      toast({ title: 'Upload Failed', message: err.message, type: 'error' })
    },
  })

  // Bulk Validate Mutation (Scoped to campaignIdParam if active)
  const validateMutation = useMutation({
    mutationFn: () => api.leads.bulkValidate({ campaign_id: campaignIdParam }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['funnel'] })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      toast({
        title: currentCampaign ? `Validation Complete (${currentCampaign.name})` : 'Validation Complete',
        message: `Evaluated ${data.total_checked} leads: ${data.valid_count} valid, ${data.invalid_count} invalid, ${data.risky_count ?? 0} risky.`,
        type: 'success',
      })
    },
    onError: (err: any) => {
      toast({ title: 'Validation Failed', message: err.message, type: 'error' })
    },
  })

  // Bulk Personalize / Generate Drafts Mutation (Scoped to campaignIdParam if active)
  const personalizeMutation = useMutation({
    mutationFn: () => api.leads.personalize({ campaign_id: campaignIdParam }),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['leads'] })
      queryClient.invalidateQueries({ queryKey: ['funnel'] })
      queryClient.invalidateQueries({ queryKey: ['campaigns'] })
      queryClient.invalidateQueries({ queryKey: ['pendingApprovals'] })
      toast({
        title: currentCampaign ? `Drafts Generated (${currentCampaign.name})` : 'Drafts Generated',
        message: data.message,
        type: 'success',
      })
    },
    onError: (err: any) => {
      toast({ title: 'Draft Generation Failed', message: err.message, type: 'error' })
    },
  })


  // Table Columns
  const columns = React.useMemo<ColumnDef<Lead, any>[]>(
    () => [
      {
        accessorKey: 'email',
        header: 'Contact / Email',
        size: 240,
        cell: ({ row }) => {
          const lead = row.original
          const fullName = [lead.first_name, lead.last_name].filter(Boolean).join(' ')
          return (
            <div className="flex flex-col min-w-0 pr-2">
              <span className="font-semibold text-foreground truncate">{lead.email}</span>
              {fullName && (
                <span className="text-[11px] text-muted-foreground truncate">{fullName}</span>
              )}
            </div>
          )
        },
      },
      {
        accessorKey: 'company',
        header: 'Company',
        size: 160,
        cell: ({ row }) => {
          const val = row.original.company
          return <span className="font-medium text-foreground truncate">{val || '—'}</span>
        },
      },
      {
        accessorKey: 'status',
        header: 'Status',
        size: 130,
        cell: ({ row }) => <StatusChip status={row.original.status} />,
      },
      {
        accessorKey: 'risk_level',
        header: 'Risk',
        size: 90,
        cell: ({ row }) => {
          const risk = row.original.risk_level || 'ok'
          return (
            <StatusChip
              status={risk}
              variant={risk === 'bad' ? 'danger' : risk === 'risky' ? 'warning' : 'success'}
            />
          )
        },
      },
      {
        accessorKey: 'country',
        header: 'Country',
        size: 110,
        cell: ({ row }) => (
          <span className="text-muted-foreground truncate">{row.original.country || '—'}</span>
        ),
      },
      {
        accessorKey: 'created_at',
        header: 'Added',
        size: 110,
        cell: ({ row }) => (
          <span className="text-muted-foreground tabular-nums">
            {new Date(row.original.created_at).toLocaleDateString([], {
              month: 'short',
              day: 'numeric',
            })}
          </span>
        ),
      },
    ],
    []
  )

  const downloadRejectsCsv = () => {
    if (!uploadReport?.reject_items) return
    const csvContent =
      'data:text/csv;charset=utf-8,' +
      'Row Number,Email,Reason\n' +
      uploadReport.reject_items.map((i: RejectItem) => `${i.row_number},"${i.email || ''}","${i.reason}"`).join('\n')
    const encodedUri = encodeURI(csvContent)
    const link = document.createElement('a')
    link.setAttribute('href', encodedUri)
    link.setAttribute('download', 'rejected_leads.csv')
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
  }

  const leads = leadsData?.items || []
  const newCount = leads.filter((ld) => ld.status === 'new').length
  const validCount = leads.filter((ld) => ld.status === 'valid').length

  return (
    <div className="space-y-4">
      {/* Top Header & Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-tight text-foreground">Leads Management</h1>
          <p className="text-xs text-muted-foreground mt-0.5">
            Filter, inspect account timelines, validate emails, and generate drafts directly from the dashboard.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {selectedLeads.length > 0 && (
            <div className="flex items-center gap-1.5 mr-2">
              <button
                type="button"
                onClick={() => {
                  selectedLeads.forEach((ld) => pauseMutation.mutate(ld.id))
                  setSelectedLeads([])
                }}
                className="px-2.5 py-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted transition-colors flex items-center gap-1"
              >
                <Pause className="w-3.5 h-3.5" /> Pause ({selectedLeads.length})
              </button>
              <button
                type="button"
                onClick={() => {
                  selectedLeads.forEach((ld) => resumeMutation.mutate(ld.id))
                  setSelectedLeads([])
                }}
                className="px-2.5 py-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted transition-colors flex items-center gap-1"
              >
                <Play className="w-3.5 h-3.5" /> Resume ({selectedLeads.length})
              </button>
            </div>
          )}

          {/* Validate Leads Button */}
          <button
            type="button"
            disabled={validateMutation.isPending}
            onClick={() => validateMutation.mutate()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md shadow-xs transition-colors disabled:opacity-60 cursor-pointer"
            title={
              currentCampaign
                ? `Validate unverified leads in "${currentCampaign.name}"`
                : 'Check email syntax, disposable domains, and DNS MX records for unverified leads'
            }
          >
            {validateMutation.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <ShieldCheck className="w-3.5 h-3.5" />
            )}
            {validateMutation.isPending ? 'Validating...' : 'Validate Leads'}
          </button>

          {/* Generate Drafts Button */}
          <button
            type="button"
            disabled={personalizeMutation.isPending}
            onClick={() => personalizeMutation.mutate()}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-md shadow-xs transition-colors disabled:opacity-60 cursor-pointer"
            title={
              currentCampaign
                ? `Run AI personalization specifically for leads in "${currentCampaign.name}"`
                : 'Run AI personalization pipeline to generate opening notes and drafts'
            }
          >
            {personalizeMutation.isPending ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Sparkles className="w-3.5 h-3.5" />
            )}
            {personalizeMutation.isPending ? 'Generating Drafts...' : 'Generate Drafts'}
          </button>

          <button
            type="button"
            onClick={() => {
              setUploadReport(null)
              setUploadFile(null)
              if (currentCampaign) {
                setUploadCampaign(currentCampaign.name)
                setUploadCampaignId(currentCampaign.id)
                setUploadCampaignMode('existing')
              } else if (campaignsData?.items?.length) {
                setUploadCampaign(campaignsData.items[0].name)
                setUploadCampaignId(campaignsData.items[0].id)
                setUploadCampaignMode('existing')
              } else {
                setUploadCampaignMode('new')
              }
              setIsUploadOpen(true)
            }}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md shadow-xs transition-colors cursor-pointer"
          >
            <Upload className="w-3.5 h-3.5" aria-hidden="true" />
            Upload CSV
          </button>
        </div>
      </div>

      {/* Active Campaign Filter Banner */}
      {currentCampaign && (
        <div className="flex items-center justify-between p-2.5 bg-indigo-500/10 border border-indigo-500/30 rounded-lg text-xs text-indigo-700 dark:text-indigo-300">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-semibold flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-indigo-500 animate-pulse" />
              Active Campaign Filter:
            </span>
            <span className="font-bold underline text-foreground">{currentCampaign.name}</span>
            <span className="text-[10px] bg-indigo-500/20 px-1.5 py-0.5 rounded-sm font-mono">
              ID #{currentCampaign.id}
            </span>
            <span className="text-[11px] text-muted-foreground">
              (Table, Validation, and Draft Generation scoped strictly to this campaign)
            </span>
          </div>
          <button
            type="button"
            onClick={() => {
              searchParams.delete('campaign')
              setSearchParams(searchParams)
            }}
            className="px-2 py-1 text-[11px] font-semibold bg-card hover:bg-card/80 text-foreground border border-border rounded-md transition-colors cursor-pointer shrink-0"
          >
            ✕ Clear Filter (Show All)
          </button>
        </div>
      )}

      {/* Workflow Step Banner */}
      {newCount > 0 && (
        <div className="flex items-center justify-between p-3 bg-amber-500/10 border border-amber-500/20 rounded-lg text-xs text-amber-700 dark:text-amber-400">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>
              <strong>Step 1:</strong> You have <strong>{newCount} unverified (New) leads</strong>. Click <strong>Validate Leads</strong> to verify email syntax, disposable checks, and DNS MX records.
            </span>
          </div>
          <button
            type="button"
            onClick={() => validateMutation.mutate()}
            disabled={validateMutation.isPending}
            className="ml-3 px-2.5 py-1 text-xs font-semibold bg-amber-600 hover:bg-amber-700 text-white rounded-md shrink-0 cursor-pointer"
          >
            {validateMutation.isPending ? 'Validating...' : 'Validate Now'}
          </button>
        </div>
      )}

      {newCount === 0 && validCount > 0 && (
        <div className="flex items-center justify-between p-3 bg-indigo-500/10 border border-indigo-500/20 rounded-lg text-xs text-indigo-700 dark:text-indigo-400">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 shrink-0" />
            <span>
              <strong>Step 2:</strong> You have <strong>{validCount} Valid leads</strong> ready for outreach. Click <strong>Generate Drafts</strong> to create personalized observations and send them to the Approval Queue.
            </span>
          </div>
          <button
            type="button"
            onClick={() => personalizeMutation.mutate()}
            disabled={personalizeMutation.isPending}
            className="ml-3 px-2.5 py-1 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-md shrink-0 cursor-pointer"
          >
            {personalizeMutation.isPending ? 'Generating...' : 'Generate Drafts'}
          </button>
        </div>
      )}


      {/* Filter Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2.5 bg-card p-3 rounded-lg border border-border text-xs">
        {/* Search */}
        <div className="relative flex-1 min-w-[200px] max-w-sm">
          <Search className="w-3.5 h-3.5 text-muted-foreground absolute left-2.5 top-2.5" />
          <input
            type="text"
            placeholder="Search email, contact, company..."
            value={search}
            onChange={(e) => {
              setSearch(e.target.value)
              setPage(1)
            }}
            className="w-full h-8 pl-8 pr-3 text-xs bg-muted/40 border border-border rounded-md focus:outline-hidden focus:ring-1 focus:ring-primary"
          />
        </div>

        {/* Filters */}
        <div className="flex flex-wrap items-center gap-2">
          {/* Status filter */}
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value)
              setPage(1)
            }}
            className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground cursor-pointer"
          >
            <option value="">All Statuses</option>
            <option value="new">New</option>
            <option value="valid">Valid</option>
            <option value="invalid">Invalid</option>
            <option value="personalized">Personalized</option>
            <option value="approved">Approved</option>
            <option value="in_sequence">In Sequence</option>
            <option value="replied">Replied</option>
            <option value="won">Won</option>
            <option value="bounced">Bounced</option>
            <option value="paused">Paused</option>
          </select>

          {/* Risk filter */}
          <select
            value={riskFilter}
            onChange={(e) => {
              setRiskFilter(e.target.value)
              setPage(1)
            }}
            className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground cursor-pointer"
          >
            <option value="">All Risk Levels</option>
            <option value="ok">OK (Low Risk)</option>
            <option value="risky">Risky (Accept-all)</option>
            <option value="bad">Bad (Failed MX)</option>
          </select>

          {/* Country filter */}
          <select
            value={countryFilter}
            onChange={(e) => {
              setCountryFilter(e.target.value)
              setPage(1)
            }}
            className="h-8 px-2.5 text-xs bg-card border border-border rounded-md text-foreground cursor-pointer"
          >
            <option value="">All Countries</option>
            <option value="United States">United States</option>
            <option value="United Kingdom">United Kingdom</option>
            <option value="Canada">Canada</option>
            <option value="Germany">Germany</option>
            <option value="France">France</option>
          </select>

          {(search || statusFilter || riskFilter || countryFilter) && (
            <button
              type="button"
              onClick={() => {
                setSearch('')
                setStatusFilter('')
                setRiskFilter('')
                setCountryFilter('')
                setPage(1)
              }}
              className="text-xs text-muted-foreground hover:text-foreground underline ml-1"
            >
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Main DataTable */}
      <DataTable
        columns={columns}
        data={leads}
        totalRows={leadsData?.total}
        page={page}
        pageSize={pageSize}
        onPageChange={setPage}
        onRowClick={(lead) => setSelectedLeadId(lead.id)}
        onSelectionChange={setSelectedLeads}
        enableSelection={true}
        isLoading={isLoading}
      />

      {/* SideDrawer: Lead Detail & Full Timeline */}
      <SideDrawer
        isOpen={selectedLeadId !== null}
        onClose={() => setSelectedLeadId(null)}
        width="lg"
        title={
          leadTimeline ? (
            <div className="flex items-center gap-2">
              <span className="truncate">{leadTimeline.email}</span>
              <StatusChip status={leadTimeline.status} />
            </div>
          ) : (
            'Lead Detail'
          )
        }
        subtitle={
          leadTimeline ? (
            <span>
              {leadTimeline.company || 'Unknown Company'} • {leadTimeline.country || 'Unknown location'} • {leadTimeline.timezone || 'No TZ'}
            </span>
          ) : undefined
        }
        footer={
          leadTimeline ? (
            <div className="w-full flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-1.5">
                {leadTimeline.status === 'paused' ? (
                  <button
                    onClick={() => resumeMutation.mutate(leadTimeline.id)}
                    className="px-2.5 py-1 text-xs font-semibold bg-emerald-600 hover:bg-emerald-700 text-white rounded-md flex items-center gap-1 shadow-xs"
                  >
                    <Play className="w-3.5 h-3.5" /> Resume
                  </button>
                ) : (
                  <button
                    onClick={() => pauseMutation.mutate(leadTimeline.id)}
                    className="px-2.5 py-1 text-xs font-semibold border border-border hover:bg-muted rounded-md flex items-center gap-1"
                  >
                    <Pause className="w-3.5 h-3.5" /> Pause
                  </button>
                )}
                <button
                  onClick={() => skipStepMutation.mutate(leadTimeline.id)}
                  className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted rounded-md flex items-center gap-1"
                >
                  <FastForward className="w-3.5 h-3.5" /> Skip Step
                </button>
              </div>

              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => markRepliedMutation.mutate(leadTimeline.id)}
                  className="px-2.5 py-1 text-xs font-medium border border-border hover:bg-muted rounded-md flex items-center gap-1"
                >
                  <MessageSquare className="w-3.5 h-3.5" /> Mark Replied
                </button>
                <button
                  onClick={() => markWonMutation.mutate(leadTimeline.id)}
                  className="px-2.5 py-1 text-xs font-semibold bg-indigo-600 hover:bg-indigo-700 text-white rounded-md flex items-center gap-1 shadow-xs"
                >
                  <Trophy className="w-3.5 h-3.5" /> Mark Won
                </button>
                <button
                  onClick={() => notInterestedMutation.mutate(leadTimeline.id)}
                  className="px-2.5 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-950/40 rounded-md flex items-center gap-1"
                >
                  <Ban className="w-3.5 h-3.5" /> Not Interested
                </button>
              </div>
            </div>
          ) : undefined
        }
      >
        {isTimelineLoading || !leadTimeline ? (
          <div className="space-y-4">
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-48 w-full" />
          </div>
        ) : (
          <div className="space-y-5 text-xs">
            {/* Lead Metadata Card */}
            <div className="p-3 bg-muted/40 rounded-lg border border-border space-y-2">
              <div className="grid grid-cols-2 gap-2 text-muted-foreground">
                <div>
                  <span className="font-medium text-foreground">Contact:</span>{' '}
                  {[leadTimeline.first_name, leadTimeline.last_name].filter(Boolean).join(' ') || '—'}
                </div>
                <div>
                  <span className="font-medium text-foreground">Title:</span>{' '}
                  {leadTimeline.title || '—'}
                </div>
                <div>
                  <span className="font-medium text-foreground">Website:</span>{' '}
                  {leadTimeline.website ? (
                    <a
                      href={leadTimeline.website}
                      target="_blank"
                      rel="noreferrer"
                      className="text-primary hover:underline truncate inline-block max-w-[140px] align-bottom"
                    >
                      {leadTimeline.website}
                    </a>
                  ) : (
                    '—'
                  )}
                </div>
                <div>
                  <span className="font-medium text-foreground">Risk Check:</span>{' '}
                  <span className="capitalize">{leadTimeline.risk_level || 'OK'}</span>
                </div>
              </div>

              {leadTimeline.personal_note && (
                <div className="pt-2 border-t border-border/60">
                  <span className="font-semibold text-foreground flex items-center gap-1">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-500" /> Personal Note:
                  </span>
                  <p className="mt-1 text-muted-foreground italic bg-card p-2 rounded-md border border-border/60 leading-relaxed">
                    "{leadTimeline.personal_note}"
                  </p>
                </div>
              )}
            </div>

            {/* Outreach Emails Rendered */}
            {leadTimeline.emails && leadTimeline.emails.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <h4 className="font-bold text-foreground flex items-center gap-1.5 uppercase text-[11px] tracking-wider text-muted-foreground">
                    <Send className="w-3.5 h-3.5" /> Dispatched Emails ({leadTimeline.emails.length})
                  </h4>
                  <span className="text-[10px] text-muted-foreground italic">Click step to view body</span>
                </div>
                <div className="space-y-2.5">
                  {leadTimeline.emails.map((msg: LeadTimelineEmail) => {
                    const isExpanded = expandedStepIds[msg.id] ?? (msg.step === 1)
                    return (
                      <div
                        key={msg.id}
                        className={`rounded-md border transition-all ${
                          isExpanded
                            ? 'bg-card border-indigo-500/40 shadow-sm'
                            : 'bg-card/70 border-border hover:border-border/80'
                        }`}
                      >
                        {/* Clickable Header */}
                        <button
                          type="button"
                          onClick={() =>
                            setExpandedStepIds((prev) => ({
                              ...prev,
                              [msg.id]: !isExpanded,
                            }))
                          }
                          className="w-full p-3 text-left flex items-center justify-between gap-2 transition-colors hover:bg-muted/30 rounded-md"
                        >
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="px-1.5 py-0.5 rounded-sm bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 text-[10px] font-semibold font-mono shrink-0">
                              Step {msg.step}
                            </span>
                            <span className="font-semibold text-foreground text-xs truncate">
                              {msg.subject}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span
                              className={`text-[10px] px-1.5 py-0.5 rounded-sm font-medium uppercase ${
                                msg.status === 'scheduled'
                                  ? 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                                  : msg.status === 'sent'
                                    ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                                    : 'bg-muted text-muted-foreground'
                              }`}
                            >
                              {msg.sent_at
                                ? new Date(msg.sent_at).toLocaleDateString()
                                : msg.status}
                            </span>
                            {isExpanded ? (
                              <ChevronUp className="w-3.5 h-3.5 text-muted-foreground" />
                            ) : (
                              <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                            )}
                          </div>
                        </button>

                        {/* Metadata Row */}
                        <div className="px-3 pb-2 text-[10px] text-muted-foreground flex items-center justify-between border-b border-border/40">
                          <span className="capitalize">
                            Status: <strong className="font-medium text-foreground">{msg.status}</strong>
                          </span>
                          {msg.scheduled_at && (
                            <span className="tabular-nums">
                              Scheduled: {new Date(msg.scheduled_at).toLocaleString()}
                            </span>
                          )}
                        </div>

                        {/* Expandable Email Body */}
                        {isExpanded && (
                          <div className="p-3 bg-muted/20 space-y-2 border-t border-border/40">
                            <div className="text-[10px] text-muted-foreground flex items-center justify-between">
                              <span className="font-medium">Rendered Email Content:</span>
                              <span className="text-[10px] text-muted-foreground">
                                {msg.step === 1 ? 'Initial Hook Email' : `Follow-up Step ${msg.step}`}
                              </span>
                            </div>
                            <div className="text-xs text-foreground bg-card p-3 rounded-md border border-border whitespace-pre-wrap font-sans leading-relaxed select-text shadow-inner">
                              {msg.body || (
                                <span className="text-muted-foreground italic">
                                  No email body generated yet.
                                </span>
                              )}
                            </div>
                          </div>
                        )}

                        {msg.error && (
                          <div className="p-2.5 text-[11px] text-danger font-mono bg-danger/10 border-t border-danger/20">
                            Error: {msg.error}
                          </div>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )}

            {/* Inbound Replies */}
            {leadTimeline.replies && leadTimeline.replies.length > 0 && (
              <div className="space-y-2">
                <h4 className="font-bold text-foreground flex items-center gap-1.5 uppercase text-[11px] tracking-wider text-muted-foreground">
                  <MessageSquare className="w-3.5 h-3.5" /> Inbound Replies ({leadTimeline.replies.length})
                </h4>
                <div className="space-y-2.5">
                  {leadTimeline.replies.map((rep: LeadTimelineReply) => (
                    <div key={rep.id} className="p-3 bg-card rounded-md border border-emerald-200 dark:border-emerald-900/60 space-y-1.5">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-foreground flex items-center gap-1.5">
                          {rep.subject}
                          <StatusChip status={rep.classification} size="sm" />
                        </span>
                        <span className="text-[10px] text-muted-foreground tabular-nums">
                          {new Date(rep.created_at).toLocaleDateString()}
                        </span>
                      </div>
                      <div className="text-[11px] text-foreground bg-emerald-50/50 dark:bg-emerald-950/20 p-2 rounded-md whitespace-pre-wrap leading-relaxed">
                        {rep.snippet}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Complete Audit Event Timeline */}
            <div className="space-y-2">
              <h4 className="font-bold text-foreground flex items-center gap-1.5 uppercase text-[11px] tracking-wider text-muted-foreground">
                <Clock className="w-3.5 h-3.5" /> Audit Trail Events ({leadTimeline.events?.length || 0})
              </h4>
              <div className="relative pl-4 border-l border-border space-y-3">
                {leadTimeline.events?.map((ev: LeadTimelineEvent) => (
                  <div key={ev.id} className="relative group">
                    <div className="absolute -left-[21px] top-1 w-2.5 h-2.5 rounded-full bg-border group-hover:bg-primary transition-colors" />
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-foreground capitalize">
                        {ev.type.replace(/_/g, ' ')}
                      </span>
                      <span className="text-[10px] text-muted-foreground tabular-nums">
                        {new Date(ev.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })},{' '}
                        {new Date(ev.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}
                      </span>
                    </div>
                    {ev.payload && (
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        {typeof ev.payload === 'object' ? JSON.stringify(ev.payload) : String(ev.payload)}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          </div>
        )}
      </SideDrawer>

      {/* CSV Upload Dialog */}
      {isUploadOpen && (
        <div
          role="dialog"
          aria-modal="true"
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in-0"
        >
          <div className="bg-card w-full max-w-lg rounded-lg border border-border shadow-2xl p-6 space-y-4">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <h3 className="text-base font-semibold text-foreground flex items-center gap-2">
                <Upload className="w-4 h-4 text-primary" /> Upload Leads CSV
              </h3>
              <button
                onClick={() => setIsUploadOpen(false)}
                className="text-muted-foreground hover:text-foreground text-sm font-semibold"
              >
                ✕
              </button>
            </div>

            {!uploadReport ? (
              <div className="space-y-4">
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-semibold text-foreground">
                      Target Campaign
                    </label>
                    <span className="text-[11px] font-medium text-primary">
                      {uploadCampaignMode === 'existing' ? 'Appending to Existing Campaign' : 'Creating New Campaign'}
                    </span>
                  </div>

                  <select
                    value={uploadCampaignMode === 'new' ? '__NEW__' : (uploadCampaignId ? String(uploadCampaignId) : '')}
                    onChange={(e) => {
                      const val = e.target.value
                      if (val === '__NEW__') {
                        setUploadCampaignMode('new')
                        setUploadCampaign('')
                        setUploadCampaignId(null)
                      } else {
                        setUploadCampaignMode('existing')
                        const selectedId = Number(val)
                        const selected = campaignsData?.items?.find((c) => c.id === selectedId)
                        if (selected) {
                          setUploadCampaign(selected.name)
                          setUploadCampaignId(selected.id)
                        }
                      }
                    }}
                    className="w-full h-9 px-3 text-xs bg-muted/30 border border-border rounded-md text-foreground cursor-pointer focus:outline-none focus:ring-1 focus:ring-primary font-medium"
                  >
                    <optgroup label="Select Existing Campaign">
                      {campaignsData?.items?.map((c) => (
                        <option key={c.id} value={c.id}>
                          {c.name} (ID #{c.id} • {c.leads_count ?? 0} total leads)
                        </option>
                      ))}
                    </optgroup>
                    <option value="__NEW__">➕ Create New Campaign...</option>
                  </select>

                  {uploadCampaignMode === 'new' && (
                    <input
                      type="text"
                      placeholder="Type new campaign name..."
                      value={uploadCampaign}
                      onChange={(e) => setUploadCampaign(e.target.value)}
                      className="w-full h-9 px-3 text-xs bg-muted/40 border border-border rounded-md focus:outline-none focus:ring-1 focus:ring-primary"
                      autoFocus
                    />
                  )}

                  <div className="p-2.5 rounded-md bg-muted/30 border border-border/60 text-[11px] text-muted-foreground leading-relaxed">
                    {uploadCampaignMode === 'existing' ? (
                      <span>
                        🎯 <strong>Existing Campaign:</strong> Nayi leads seedha is campaign me add ho jayengi. Pehle se maujood emails automatically duplicate ke tor par skip ho jayengi.
                      </span>
                    ) : (
                      <span>
                        ✨ <strong>New Campaign:</strong> Diye gaye naam ke sath nayi campaign banegi aur leads usme import hon gi.
                      </span>
                    )}
                  </div>
                </div>

                {/* Drag and Drop Zone */}
                <div
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => {
                    e.preventDefault()
                    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
                      setUploadFile(e.dataTransfer.files[0])
                    }
                  }}
                  className="border-2 border-dashed border-border rounded-lg p-6 text-center hover:bg-muted/30 transition-colors flex flex-col items-center justify-center cursor-pointer"
                  onClick={() => document.getElementById('csv-file-input')?.click()}
                >
                  <input
                    id="csv-file-input"
                    type="file"
                    accept=".csv,text/csv"
                    className="hidden"
                    onChange={(e) => {
                      if (e.target.files && e.target.files[0]) {
                        setUploadFile(e.target.files[0])
                      }
                    }}
                  />
                  <Upload className="w-8 h-8 text-muted-foreground mb-2" />
                  <p className="text-xs font-semibold text-foreground">
                    {uploadFile ? uploadFile.name : 'Drag & drop leads CSV file here or browse'}
                  </p>
                  <p className="text-[11px] text-muted-foreground mt-1">
                    Columns: email, first_name, last_name, company, website, country, personal_note
                  </p>
                </div>

                <div className="flex justify-end gap-2 pt-2">
                  <button
                    type="button"
                    onClick={() => setIsUploadOpen(false)}
                    className="px-3 py-1.5 text-xs font-medium border border-border rounded-md hover:bg-muted"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    disabled={!uploadFile || uploadMutation.isPending || (uploadCampaignMode === 'new' && !uploadCampaign.trim())}
                    onClick={() => {
                      if (uploadFile) {
                        uploadMutation.mutate({
                          file: uploadFile,
                          campaign: uploadCampaign,
                          campaignId: uploadCampaignId || undefined,
                        })
                      }
                    }}
                    className="px-3.5 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md shadow-xs disabled:opacity-50 cursor-pointer"
                  >
                    {uploadMutation.isPending ? 'Processing Import...' : 'Import Leads'}
                  </button>
                </div>
              </div>
            ) : (
              /* Import Report Display */
              <div className="space-y-4">
                <div className="p-4 bg-muted/40 rounded-lg border border-border space-y-3 text-xs">
                  <div className="flex items-center justify-between pb-2 border-b border-border/80">
                    <span className="font-semibold text-foreground">Campaign:</span>
                    <span className="font-bold text-foreground">{uploadReport.campaign_name}</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3 tabular-nums">
                    <div>
                      <span className="text-muted-foreground">Total Rows:</span>{' '}
                      <strong className="text-foreground">{uploadReport.total_rows}</strong>
                    </div>
                    <div>
                      <span className="text-emerald-600 dark:text-emerald-400 font-semibold">Imported:</span>{' '}
                      <strong className="text-emerald-600 dark:text-emerald-400">{uploadReport.imported}</strong>
                    </div>
                    <div>
                      <span className="text-muted-foreground">Duplicates:</span>{' '}
                      <strong className="text-foreground">{uploadReport.duplicates}</strong>
                    </div>
                    <div>
                      <span className="text-amber-600 dark:text-amber-400">Suppressed:</span>{' '}
                      <strong className="text-amber-600 dark:text-amber-400">{uploadReport.suppressed}</strong>
                    </div>
                    <div>
                      <span className="text-red-600 dark:text-red-400 font-semibold">Rejected:</span>{' '}
                      <strong className="text-red-600 dark:text-red-400">{uploadReport.rejected}</strong>
                    </div>
                  </div>
                </div>

                {uploadReport.reject_items && uploadReport.reject_items.length > 0 && (
                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-red-600 dark:text-red-400 flex items-center gap-1">
                        <AlertCircle className="w-3.5 h-3.5" /> Rejections Breakdown
                      </span>
                      <button
                        onClick={downloadRejectsCsv}
                        className="text-xs text-primary font-semibold hover:underline flex items-center gap-1"
                      >
                        <Download className="w-3 h-3" /> Download Rejects CSV
                      </button>
                    </div>
                    <div className="max-h-36 overflow-y-auto space-y-1 border border-border rounded-md p-2 bg-muted/20 text-[11px]">
                      {uploadReport.reject_items.slice(0, 10).map((rej: RejectItem, idx: number) => (
                        <div key={idx} className="flex justify-between text-muted-foreground">
                          <span>Row {rej.row_number}: {rej.email || 'No email'}</span>
                          <span className="text-red-500 font-medium">{rej.reason}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="flex justify-end pt-2">
                  <button
                    type="button"
                    onClick={() => setIsUploadOpen(false)}
                    className="px-4 py-1.5 text-xs font-semibold bg-primary hover:bg-primary/90 text-primary-foreground rounded-md"
                  >
                    Close Report
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
