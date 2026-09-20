'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useQuery } from '@tanstack/react-query'
import { toast } from 'sonner'
import {
  BarChart3,
  Building2,
  Calendar,
  Clock,
  ExternalLink,
  Eye,
  FileDown,
  Mail,
  MapPin,
  MonitorPlay,
  Receipt,
  Search,
  Share2,
  TrendingUp,
  Users,
} from 'lucide-react'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { EmailReportModal } from '@/components/dashboard/email-report-modal'
import { EditClientAdModal } from '@/components/dashboard/edit-client-ad-modal'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsIndicator, TabsList, TabsPanel, TabsTrigger } from '@/components/ui/tabs'
import { api, resolveMediaUrl } from '@/lib/api'
import type { Placement } from '@/lib/types'
import { bookingState } from '@/lib/format'

const asDate = (iso: string) =>
  new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

export default function CampaignsPage() {
  const [activeTab, setActiveTab] = useState<'placements' | 'analytics'>('placements')
  const [filterState, setFilterState] = useState<'all' | 'running' | 'scheduled' | 'ended'>('all')
  const [search, setSearch] = useState('')
  const [selectedPlacementForEmail, setSelectedPlacementForEmail] = useState<Placement | null>(null)
  const [editingContentId, setEditingContentId] = useState<number | null>(null)

  // One user-initiated fetch when the editor opens. The modal renders nothing while
  // contentItem is null, so this needs no loading branch of its own.
  const editingContentQuery = useQuery({
    queryKey: ['content', editingContentId],
    queryFn: () => api.getContentItem(editingContentId as number),
    enabled: editingContentId !== null,
  })
  const [busyReportId, setBusyReportId] = useState<number | null>(null)

  const placementsQuery = useQuery({
    queryKey: ['all-placements'],
    queryFn: api.getAllPlacements,
  })

  const legacyCampaignsQuery = useQuery({
    queryKey: ['campaigns'],
    queryFn: api.getCampaigns,
  })

  const placements = placementsQuery.data || []
  const legacyCampaigns = legacyCampaignsQuery.data || []

  // Filtered placements
  const filteredPlacements = useMemo(() => {
    const term = search.trim().toLowerCase()
    return placements.filter((p) => {
      const state = bookingState(p)
      const matchesFilter = filterState === 'all' || state.label.toLowerCase() === filterState
      const matchesSearch =
        !term ||
        p.advertiser.toLowerCase().includes(term) ||
        (p.creative_name && p.creative_name.toLowerCase().includes(term)) ||
        (p.client?.name && p.client.name.toLowerCase().includes(term)) ||
        (p.client?.client_code && p.client.client_code.toLowerCase().includes(term)) ||
        p.targets.some((t) => t.name.toLowerCase().includes(term))
      return matchesFilter && matchesSearch
    })
  }, [placements, filterState, search])

  // Aggregate Metrics
  const metrics = useMemo(() => {
    // Through the shared helper rather than re-deriving the dates here. Two answers to
    // "is this running" on one page meant the tile could count a campaign the row beside
    // it labelled Ended -- and calling Date.now() inside a memo is what the purity rule
    // objects to, correctly: the result is not a function of the dependencies.
    const active = placements.filter((p) => bookingState(p).label === 'Running')
    // Counted over RUNNING campaigns only: a plan that finished under-filled is history,
    // and there is nothing an operator can do about it now.
    const screensUnused = active.reduce((sum, p) => sum + (p.screens_unused || 0), 0)
    const activeScreens = new Set(
      active.flatMap((p) => p.targets.filter((t) => t.screen_id).map((t) => t.screen_id!)),
    ).size

    return {
      activeCount: active.length,
      totalCount: placements.length,
      screensUnused,
      activeScreens,
    }
  }, [placements])

  const handleDownload = async (placement: Placement) => {
    setBusyReportId(placement.id)
    try {
      await api.downloadBookingReport(placement.id)
      toast.success('Playback report downloaded.')
    } catch (err) {
      toast.error((err as Error).message || 'Could not download report.')
    } finally {
      setBusyReportId(null)
    }
  }

  const handleShare = async (placement: Placement) => {
    setBusyReportId(placement.id)
    try {
      const outcome = await api.shareBookingReport(placement.id, `Playback Report — ${placement.advertiser}`)
      if (outcome === 'downloaded') {
        toast.info('Sharing is not available in this browser; the report was downloaded instead.')
      }
    } catch (err) {
      toast.error((err as Error).message || 'Could not generate report.')
    } finally {
      setBusyReportId(null)
    }
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Advertising & Proof of Play"
        title="Campaigns & Placements"
        description="Where each client's advert ran and how it performed. Billing lives under Invoices."
      />

      {/* Metric Tiles */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div className="ring-hairline bg-card rounded-2xl p-5 ring-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">Active Campaigns</span>
            <span className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 grid size-8 place-items-center rounded-lg">
              <TrendingUp className="size-4" />
            </span>
          </div>
          <p className="text-foreground mt-3 text-3xl font-bold tabular-nums">{metrics.activeCount}</p>
          <p className="text-muted-foreground mt-1 text-xs">{metrics.totalCount} total bookings on file</p>
        </div>

        {/* Revenue used to be totted up here, on a page the nav calls "Playback report".
            Money now lives on /dashboard/invoices; this page is delivery evidence. What
            belongs here instead is whether the plans clients paid for are being filled. */}
        <div className="ring-hairline bg-card rounded-2xl p-5 ring-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">Unused screens</span>
            <span className="bg-primary/10 text-primary dark:text-brand grid size-8 place-items-center rounded-lg">
              <MonitorPlay className="size-4" />
            </span>
          </div>
          <p className="text-foreground mt-3 text-3xl font-bold tabular-nums font-mono">
            {metrics.screensUnused}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">Paid for on a plan and not running</p>
        </div>

        <div className="ring-hairline bg-card rounded-2xl p-5 ring-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">Screens On Air</span>
            <span className="bg-sky-500/10 text-sky-600 dark:text-sky-400 grid size-8 place-items-center rounded-lg">
              <MonitorPlay className="size-4" />
            </span>
          </div>
          <p className="text-foreground mt-3 text-3xl font-bold tabular-nums">{metrics.activeScreens}</p>
          <p className="text-muted-foreground mt-1 text-xs">Carrying active ad rotations</p>
        </div>

        <div className="ring-hairline bg-card rounded-2xl p-5 ring-1 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground text-xs font-semibold uppercase tracking-wider">Clients</span>
            <span className="bg-amber-500/10 text-amber-600 dark:text-amber-400 grid size-8 place-items-center rounded-lg">
              <Users className="size-4" />
            </span>
          </div>
          <p className="text-foreground mt-3 text-3xl font-bold tabular-nums">
            {new Set(placements.map((p) => p.client?.id || p.advertiser)).size}
          </p>
          <p className="text-muted-foreground mt-1 text-xs">Advertisers booked</p>
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={(val) => setActiveTab(val as 'placements' | 'analytics')}>
        <TabsList>
          <TabsTrigger value="placements">Client Ad Placements</TabsTrigger>
          <TabsTrigger value="analytics">Playlist Campaign Analytics</TabsTrigger>
          <TabsIndicator />
        </TabsList>

        {/* Tab 1: Client Ad Placements */}
        <TabsPanel value="placements" className="space-y-6 pt-4">
          {/* Controls Bar */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap items-center gap-1.5 bg-muted/50 p-1 rounded-xl border border-border/50">
              {(['all', 'running', 'scheduled', 'ended'] as const).map((st) => (
                <button
                  key={st}
                  onClick={() => setFilterState(st)}
                  className={`px-3 py-1 text-xs font-medium rounded-lg transition-colors capitalize ${
                    filterState === st
                      ? 'bg-card text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {st === 'all' ? 'All Bookings' : st}
                </button>
              ))}
            </div>

            <div className="relative max-w-sm w-full">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
              <Input
                placeholder="Search by client, creative, screen..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9 text-xs"
              />
            </div>
          </div>

          {placementsQuery.isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-20 rounded-xl" />
              ))}
            </div>
          ) : placementsQuery.isError ? (
            <ErrorState message="Could not load ad placements." onRetry={() => placementsQuery.refetch()} />
          ) : !filteredPlacements.length ? (
            <EmptyState
              icon={Receipt}
              title={search ? 'No matching bookings found' : 'No ad placements booked yet'}
              description={
                search
                  ? 'Try clearing your search query or filters.'
                  : 'To sell ad time, open any uploaded media item in Content library and book it to a client.'
              }
              action={
                !search ? (
                  <Button variant="outline" render={<Link href="/dashboard/content" />}>
                    View Content Library
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <div className="divide-hairline divide-y rounded-2xl border border-border/60 bg-card overflow-hidden shadow-sm">
              {filteredPlacements.map((placement) => {
                const state = bookingState(placement)
                const isBusy = busyReportId === placement.id
                const clientName = placement.client?.name || placement.advertiser

                return (
                  <div
                    key={placement.id}
                    className="p-4 sm:p-5 flex flex-col lg:flex-row lg:items-center justify-between gap-4 hover:bg-muted/20 transition-colors"
                  >
                    {/* Left: Creative & Client */}
                    <div className="flex items-start gap-3.5 min-w-0 flex-1">
                      {placement.creative_thumbnail_url ? (
                        <img
                          src={resolveMediaUrl(placement.creative_thumbnail_url)}
                          alt=""
                          className="size-14 rounded-xl object-cover border border-border/40 shrink-0 bg-muted"
                        />
                      ) : (
                        <div className="size-14 rounded-xl border border-border/40 bg-muted/60 grid place-items-center shrink-0">
                          <BarChart3 className="size-6 text-muted-foreground/60" />
                        </div>
                      )}

                      <div className="min-w-0 space-y-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="font-semibold text-foreground text-sm truncate">{clientName}</h3>
                          <Badge variant={state.tone} className="text-[11px] capitalize">
                            {state.label}
                          </Badge>
                          {placement.client?.client_code && (
                            <Badge variant="outline" className="text-[10px] font-mono">
                              {placement.client.client_code}
                            </Badge>
                          )}
                          {/* Paid / unpaid moved to /dashboard/invoices. A report a client
                              may be forwarded should not carry their payment status. */}
                          {placement.screens_unused > 0 && (
                            <Badge variant="outline" className="text-[10px] text-amber-600 border-amber-500/30">
                              {placement.screens_unused} screen{placement.screens_unused === 1 ? '' : 's'} unused
                            </Badge>
                          )}
                        </div>

                        <p className="text-xs text-muted-foreground truncate">
                          Creative:{' '}
                          <Link
                            href={`/dashboard/content/${placement.content_id}`}
                            className="font-medium text-foreground hover:underline inline-flex items-center gap-1"
                          >
                            {placement.creative_name || `Asset #${placement.content_id}`}
                            <ExternalLink className="size-2.5 opacity-60" />
                          </Link>
                          {placement.plan && ` • Plan: ${placement.plan.name}`}
                        </p>

                        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground pt-1">
                          <span className="flex items-center gap-1">
                            <Calendar className="size-3.5 shrink-0" />
                            {asDate(placement.starts_at)} → {asDate(placement.effective_ends_at || placement.ends_at)}
                          </span>

                          {placement.days_remaining !== undefined && placement.days_remaining !== null && state.label === 'Running' && (
                            <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                              <Clock className="size-3.5 shrink-0" />
                              {placement.days_remaining} day{placement.days_remaining === 1 ? '' : 's'} left
                            </span>
                          )}

                          <span className="flex items-center gap-1">
                            <MapPin className="size-3.5 shrink-0" />
                            {placement.targets.length} place{placement.targets.length === 1 ? '' : 's'}:{' '}
                            {placement.targets.map((t) => t.name).slice(0, 2).join(', ')}
                            {placement.targets.length > 2 && ` +${placement.targets.length - 2} more`}
                          </span>
                        </div>
                      </div>
                    </div>

                    {/* Right: Revenue & Actions */}
                    <div className="flex flex-wrap sm:flex-nowrap items-center justify-between lg:justify-end gap-3 shrink-0 pt-2 lg:pt-0 border-t lg:border-t-0 border-border/40">
                      <div className="text-left lg:text-right">
                        <p className="text-base font-bold font-mono text-foreground">
                          {placement.days_remaining ?? 0}d left
                        </p>
                        {placement.extensions.length > 0 && (
                          <p className="text-[11px] text-muted-foreground">
                            {placement.extensions.length} extension{placement.extensions.length === 1 ? '' : 's'} included
                          </p>
                        )}
                      </div>

                      <div className="flex items-center gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            setEditingContentId(placement.content_id)
                          }
                          title="Edit Client & Ad Details"
                          className="h-8 px-2 text-xs text-primary"
                        >
                          <Building2 className="size-3.5" />
                        </Button>

                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleDownload(placement)}
                          disabled={isBusy}
                          title="Download PDF Playback Report"
                          className="h-8 px-2.5 text-xs gap-1"
                        >
                          <FileDown className="size-3.5" /> PDF
                        </Button>

                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleShare(placement)}
                          disabled={isBusy}
                          title="Share Report"
                          className="h-8 px-2 text-xs"
                        >
                          <Share2 className="size-3.5" />
                        </Button>

                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setSelectedPlacementForEmail(placement)}
                          title="Email Report to Client"
                          className="h-8 px-2 text-xs text-primary dark:text-brand"
                        >
                          <Mail className="size-3.5" />
                        </Button>

                        <Button
                          variant="ghost"
                          size="sm"
                          render={<Link href={`/dashboard/content/${placement.content_id}`} />}
                          title="View Asset Details"
                          className="h-8 px-2 text-xs"
                        >
                          <Eye className="size-3.5" />
                        </Button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </TabsPanel>

        {/* Tab 2: Playlist Campaign Analytics */}
        <TabsPanel value="analytics" className="space-y-6 pt-4">
          {legacyCampaignsQuery.isLoading ? (
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 3 }).map((_, index) => (
                <Skeleton key={index} className="h-40" />
              ))}
            </div>
          ) : !legacyCampaigns.length ? (
            <EmptyState
              icon={BarChart3}
              title="No playlist campaigns yet"
              description="Assign a campaign to a playlist and its playback analytics will appear here."
            />
          ) : (
            <div className="grid grid-cols-1 gap-5 md:grid-cols-2 xl:grid-cols-3">
              {legacyCampaigns.map((campaign) => (
                <Link
                  key={campaign.id}
                  href={`/dashboard/campaigns/${campaign.id}`}
                  className="group focus-visible:ring-primary rounded-2xl focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none"
                >
                  <Card className="ring-hairline bg-card h-full border-0 py-0 shadow-sm ring-1 transition-shadow hover:shadow-md">
                    <CardContent className="p-5">
                      <span className="bg-primary/10 text-primary dark:text-brand grid size-11 place-items-center rounded-xl">
                        <BarChart3 className="size-5" aria-hidden="true" />
                      </span>
                      <h2 className="text-foreground mt-4 font-semibold tracking-[-0.02em]">{campaign.name}</h2>
                      <p className="text-muted-foreground/70 mt-1 text-sm">View detailed timeseries analytics</p>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          )}
        </TabsPanel>
      </Tabs>

      {/* Email Report Dispatch Modal */}
      <EmailReportModal
        placement={selectedPlacementForEmail}
        open={Boolean(selectedPlacementForEmail)}
        onOpenChange={(open) => {
          if (!open) setSelectedPlacementForEmail(null)
        }}
      />

      {/* Unified Edit Client & Ad Details Modal */}
      <EditClientAdModal
        open={editingContentId !== null}
        onOpenChange={(open) => {
          if (!open) setEditingContentId(null)
        }}
        contentItem={editingContentQuery.data ?? null}
      />
    </div>
  )
}
