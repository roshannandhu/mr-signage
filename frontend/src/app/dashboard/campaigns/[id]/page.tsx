'use client'

import { useQuery } from '@tanstack/react-query'
import Link from 'next/link'
import { useParams } from 'next/navigation'
import { useState } from 'react'
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { ArrowLeft, BarChart3, CheckCircle2, Download, MonitorPlay } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import type { CampaignExportFormat } from '@/lib/types'

const exportFormats: { value: CampaignExportFormat; label: string }[] = [
  { value: 'csv', label: 'CSV' },
  { value: 'excel', label: 'Excel' },
  { value: 'pdf', label: 'PDF' },
]

export default function CampaignAnalyticsPage() {
  const { id } = useParams<{ id: string }>()
  const campaignId = Number(id)
  const enabled = Number.isFinite(campaignId)
  const [exporting, setExporting] = useState<CampaignExportFormat | null>(null)

  const infoQuery = useQuery({ queryKey: ['campaign', campaignId, 'info'], queryFn: () => api.getCampaign(campaignId), enabled })
  const statsQuery = useQuery({ queryKey: ['campaign', campaignId, 'stats'], queryFn: () => api.getCampaignStats(campaignId), enabled })
  const timeseriesQuery = useQuery({ queryKey: ['campaign', campaignId, 'timeseries'], queryFn: () => api.getCampaignTimeseries(campaignId), enabled })

  const handleExport = async (format: CampaignExportFormat) => {
    setExporting(format)
    try {
      await api.downloadCampaignReport(campaignId, format)
    } catch (reason) {
      toast.error(reason instanceof Error ? reason.message : 'The report could not be exported')
    } finally {
      setExporting(null)
    }
  }

  if (infoQuery.isError || statsQuery.isError) {
    return <ErrorState message="Campaign analytics could not be loaded." onRetry={() => { infoQuery.refetch(); statsQuery.refetch(); timeseriesQuery.refetch() }} />
  }

  const info = infoQuery.data
  const stats = statsQuery.data
  const timeseries = timeseriesQuery.data || []

  if (!info || !stats) {
    return <div className="space-y-6"><Skeleton className="h-24" /><div className="grid grid-cols-1 gap-4 md:grid-cols-4">{Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-32" />)}</div><Skeleton className="h-[420px]" /></div>
  }

  const cards = [
    { label: 'Assigned screens', value: info.assigned_screens.toLocaleString(), icon: MonitorPlay, note: `${info.online} online · ${info.currently_playing} playing` },
    { label: 'Plays today', value: stats.today.total_plays.toLocaleString(), icon: BarChart3, note: `${stats.today.success_percent}% successful` },
    { label: 'Plays this week', value: stats.week.total_plays.toLocaleString(), icon: BarChart3, note: `${stats.week.success_percent}% successful` },
    {
      label: 'Lifetime plays', value: stats.lifetime.total_plays.toLocaleString(), icon: CheckCircle2,
      note: `${stats.lifetime.online_plays.toLocaleString()} online · ${stats.lifetime.offline_plays.toLocaleString()} offline`,
    },
  ]

  return (
    <div className="space-y-8">
      <Link href="/dashboard/campaigns" className="text-muted-foreground hover:text-foreground inline-flex items-center gap-2 text-sm font-medium">
        <ArrowLeft className="size-4" /> Back to campaigns
      </Link>

      <PageHeader
        eyebrow="Reporting"
        title={info.name}
        description="Detailed performance and proof-of-play metrics for this campaign."
        actions={
          <div className="flex flex-wrap gap-2">
            {exportFormats.map(({ value, label }) => (
              <Button key={value} variant="outline" className="bg-card" disabled={exporting !== null} onClick={() => handleExport(value)}>
                <Download data-icon="inline-start" /> {exporting === value ? 'Preparing…' : label}
              </Button>
            ))}
          </div>
        }
      />

      <section aria-label="Campaign statistics" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map(({ label, value, icon: Icon, note }) => (
          <Card key={label} className="ring-hairline bg-card border-0 py-0 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1">
            <CardContent className="p-5">
              <div className="flex items-start justify-between">
                <p className="text-muted-foreground text-sm font-medium">{label}</p>
                <span className="bg-primary/10 text-primary dark:text-brand grid size-9 place-items-center rounded-xl"><Icon className="size-4" aria-hidden="true" /></span>
              </div>
              <p className="text-foreground mt-5 text-3xl font-semibold tabular-nums tracking-[-0.04em]">{value}</p>
              <p className="text-muted-foreground/70 mt-1 text-xs">{note}</p>
            </CardContent>
          </Card>
        ))}
      </section>

      <Card className="ring-hairline bg-card border-0 py-0 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1">
        <CardHeader className="pt-5">
          <CardTitle>Proof of play — last 7 days</CardTitle>
          <CardDescription>Hourly rollups of completed plays.</CardDescription>
        </CardHeader>
        <CardContent className="h-[400px] pb-5">
          {timeseries.length ? (
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={timeseries} margin={{ top: 10, right: 10, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                <XAxis dataKey="date" stroke="var(--muted-foreground)" fontSize={12} tickLine={false} axisLine={false} />
                <YAxis stroke="var(--muted-foreground)" fontSize={12} tickLine={false} axisLine={false} />
                <Tooltip
                  contentStyle={{ backgroundColor: 'var(--popover)', border: '1px solid var(--border)', borderRadius: '10px' }}
                  itemStyle={{ color: 'var(--popover-foreground)' }}
                  labelStyle={{ color: 'var(--muted-foreground)' }}
                />
                <Bar dataKey="completed_plays" fill="var(--chart-1)" radius={[4, 4, 0, 0]} name="Completed plays" />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div className="text-muted-foreground/70 flex h-full items-center justify-center text-sm">
              No playback recorded in the last 7 days.
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
