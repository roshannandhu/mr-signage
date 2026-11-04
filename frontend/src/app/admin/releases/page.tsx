'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  Info,
  Package,
  PackagePlus,
  PauseCircle,
  Rocket,
  X,
} from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { RolloutState } from '@/lib/types'
import { relativeTime } from '@/lib/format'
import { PageHeader } from '@/components/admin/admin-ui'

export default function AdminReleasesPage() {
  const queryClient = useQueryClient()
  const [publishOpen, setPublishOpen] = useState(false)
  const [versionCode, setVersionCode] = useState('')
  const [versionName, setVersionName] = useState('')
  const [apkUrl, setApkUrl] = useState('')
  const [sha256, setSha256] = useState('')
  const [initialRing, setInitialRing] = useState<RolloutState>('canary')

  const releasesQuery = useQuery({ queryKey: ['admin', 'releases'], queryFn: api.getReleases })

  const createMutation = useMutation({
    mutationFn: async (payload: {
      version_code: number
      version_name: string
      apk_url: string
      sha256: string
      mandatory: boolean
      rollout_state: RolloutState
    }) => {
      // Create release
      const release = await api.createRelease({
        version_code: payload.version_code,
        version_name: payload.version_name,
        apk_url: payload.apk_url,
        sha256: payload.sha256,
        mandatory: payload.mandatory,
      })
      // If initial ring is not draft, promote it
      if (payload.rollout_state !== 'draft') {
        return await api.promoteRelease(payload.version_code, payload.rollout_state)
      }
      return release
    },
    onSuccess: (rel) => {
      toast.success(`v${rel.version_code} published to ${rel.rollout_state} ring`)
      queryClient.invalidateQueries({ queryKey: ['admin', 'releases'] })
      setPublishOpen(false)
      setVersionCode('')
      setVersionName('')
      setApkUrl('')
      setSha256('')
      setInitialRing('canary')
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to publish release'),
  })

  const promoteMutation = useMutation({
    mutationFn: ({ versionCode, rolloutState }: { versionCode: number; rolloutState: RolloutState }) =>
      api.promoteRelease(versionCode, rolloutState),
    onSuccess: (release) => {
      toast.success(
        release.rollout_state === 'released'
          ? `v${release.version_code} is now live across all unpinned fleet TVs`
          : release.rollout_state === 'draft'
            ? `v${release.version_code} rollout paused (Draft ring)`
            : `v${release.version_code} ring changed to Canary`,
      )
      queryClient.invalidateQueries({ queryKey: ['admin', 'releases'] })
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to change the rollout ring'),
  })

  const releases = releasesQuery.data || []
  const latestReleased = releases.reduce<number | null>(
    (max, release) => (release.rollout_state === 'released' ? Math.max(max ?? 0, release.version_code) : max),
    null,
  )

  const isShaValid = /^[0-9a-fA-F]{64}$/.test(sha256.trim())

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Deployment Studio & OTA Rollouts"
        description="Release Android TV player APK builds with phased rollout rings and automated device convergence"
      >
        <button
          onClick={() => setPublishOpen(true)}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow-sm hover:bg-primary/90"
        >
          <PackagePlus className="size-4" />
          Publish New Release
        </button>
      </PageHeader>

      {/* Android Version Downgrade Rule Callout */}
      <div className="flex items-start gap-3 rounded-2xl border border-border bg-card p-4 text-xs text-muted-foreground">
        <Info className="mt-0.5 size-4 shrink-0 text-primary" />
        <div className="space-y-1">
          <p className="font-semibold text-foreground">Android OS Package Manager Safety Contract</p>
          <p>
            Android TV devices will never downgrade to an earlier <code className="font-mono text-foreground">version_code</code>.
            If a release has a bug, pausing rollout stops unupdated TVs from receiving it. To replace an issue in production,
            publish a hotfix build with a strictly higher version code.
          </p>
        </div>
      </div>

      {/* Rollout Timeline Visualizer */}
      <section className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <div className="rounded-2xl border border-border bg-card p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Ring 1: Draft</span>
            <span className="rounded-md border border-border bg-muted/60 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground">
              Internal
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Zero automatic distribution. TV players never pull draft builds unless explicitly targeted during offline diagnostics.
          </p>
          <div className="mt-3 text-xs font-medium text-foreground">
            {releases.filter((r) => r.rollout_state === 'draft').length} builds in draft
          </div>
        </div>

        <div className="rounded-2xl border border-primary/30 bg-primary/5 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-primary">Ring 2: Canary</span>
            <span className="rounded-md border border-primary/20 bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">
              Staging / Pilot
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Targeted delivery. Only screens specifically pinned to this version code will receive and install the update.
          </p>
          <div className="mt-3 text-xs font-medium text-foreground">
            {releases.filter((r) => r.rollout_state === 'canary').length} builds in canary
          </div>
        </div>

        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/5 p-4">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold uppercase tracking-wider text-emerald-700 dark:text-emerald-400">
              Ring 3: Released
            </span>
            <span className="rounded-md border border-emerald-500/20 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">
              Global Default
            </span>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Fleet-wide default. All unpinned online TVs converge to the highest released version code during sync.
          </p>
          <div className="mt-3 text-xs font-medium text-foreground">
            {latestReleased ? `Current default: v${latestReleased}` : 'No active fleet release'}
          </div>
        </div>
      </section>

      {/* Releases History Table */}
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border p-4">
          <div className="flex items-center gap-2">
            <Package className="size-4 text-foreground" />
            <h2 className="text-sm font-semibold text-foreground">Release Pipeline Registry</h2>
          </div>
          <span className="text-xs text-muted-foreground">{releases.length} published builds</span>
        </div>

        {releasesQuery.isLoading ? (
          <p className="p-8 text-center text-xs text-muted-foreground">Loading releases…</p>
        ) : releases.length === 0 ? (
          <p className="p-8 text-center text-xs text-muted-foreground">No releases published yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[800px] text-sm">
              <thead className="border-b border-border bg-muted/40 text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-4 pl-5 text-left">Version</th>
                  <th className="p-4 text-left">Rollout Ring</th>
                  <th className="p-4 text-left">Binary & Checksum</th>
                  <th className="p-4 text-left">Published</th>
                  <th className="p-4 pr-5 text-right">Promote / Control</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {releases.map((rel) => {
                  const isLatestDefault = rel.version_code === latestReleased
                  return (
                    <tr key={rel.id} className="transition-colors hover:bg-muted/30">
                      <td className="p-4 pl-5">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-bold text-foreground">v{rel.version_code}</span>
                          <span className="text-xs text-muted-foreground">({rel.version_name})</span>
                          {isLatestDefault && (
                            <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-semibold text-emerald-700 dark:text-emerald-400">
                              Fleet Default
                            </span>
                          )}
                        </div>
                      </td>

                      <td className="p-4">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${
                            rel.rollout_state === 'released'
                              ? 'border border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                              : rel.rollout_state === 'canary'
                                ? 'border border-primary/20 bg-primary/10 text-primary'
                                : 'border border-border bg-muted text-muted-foreground'
                          }`}
                        >
                          <span
                            className={`size-1.5 rounded-full ${
                              rel.rollout_state === 'released'
                                ? 'bg-emerald-500'
                                : rel.rollout_state === 'canary'
                                  ? 'bg-primary'
                                  : 'bg-muted-foreground'
                            }`}
                          />
                          {rel.rollout_state === 'released'
                            ? 'Released'
                            : rel.rollout_state === 'canary'
                              ? 'Canary Ring'
                              : 'Draft / Paused'}
                        </span>
                      </td>

                      <td className="p-4">
                        <div className="flex flex-col text-xs">
                          <a
                            href={rel.apk_url}
                            target="_blank"
                            rel="noreferrer"
                            className="flex items-center gap-1 text-primary hover:underline"
                          >
                            <Download className="size-3" />
                            Download APK
                          </a>
                          <span className="font-mono text-[11px] text-muted-foreground" title={rel.sha256 || 'None'}>
                            SHA: {rel.sha256 ? `${rel.sha256.slice(0, 14)}…` : 'No checksum'}
                          </span>
                        </div>
                      </td>

                      <td className="p-4 text-xs text-muted-foreground">{relativeTime(rel.created_at)}</td>

                      <td className="p-4 pr-5 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {rel.rollout_state !== 'draft' && (
                            <button
                              onClick={() =>
                                promoteMutation.mutate({
                                  versionCode: rel.version_code,
                                  rolloutState: 'draft',
                                })
                              }
                              disabled={promoteMutation.isPending}
                              title="Pause rollout: Prevents any more screens from downloading this build"
                              className="flex items-center gap-1 rounded-lg border border-border bg-card px-2.5 py-1 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                            >
                              <PauseCircle className="size-3" />
                              Pause Rollout
                            </button>
                          )}

                          {rel.rollout_state !== 'canary' && (
                            <button
                              onClick={() =>
                                promoteMutation.mutate({
                                  versionCode: rel.version_code,
                                  rolloutState: 'canary',
                                })
                              }
                              disabled={promoteMutation.isPending}
                              className="flex items-center gap-1 rounded-lg border border-primary/20 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/20"
                            >
                              Move to Canary
                            </button>
                          )}

                          {rel.rollout_state !== 'released' && (
                            <button
                              onClick={() =>
                                promoteMutation.mutate({
                                  versionCode: rel.version_code,
                                  rolloutState: 'released',
                                })
                              }
                              disabled={promoteMutation.isPending}
                              className="flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-500"
                            >
                              <Rocket className="size-3" />
                              Deploy Global
                            </button>
                          )}
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Publish Wizard Modal (Defaults to Canary!) */}
      {publishOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-lg rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border pb-4">
              <div className="flex items-center gap-2">
                <Rocket className="size-5 text-primary" />
                <h3 className="text-base font-semibold text-foreground">Publish Player APK Release</h3>
              </div>
              <button
                onClick={() => setPublishOpen(false)}
                className="rounded-lg p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <X className="size-5" />
              </button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault()
                createMutation.mutate({
                  version_code: parseInt(versionCode, 10),
                  version_name: versionName.trim(),
                  apk_url: apkUrl.trim(),
                  sha256: sha256.trim().toLowerCase(),
                  mandatory: false,
                  rollout_state: initialRing,
                })
              }}
              className="mt-4 space-y-4"
            >
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-xs font-medium text-foreground">Version Code (integer)</label>
                  <input
                    type="number"
                    min={1}
                    required
                    placeholder="e.g. 12"
                    value={versionCode}
                    onChange={(e) => setVersionCode(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:outline-none focus:border-primary"
                  />
                </div>
                <div>
                  <label className="text-xs font-medium text-foreground">Version Name</label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. 2.1.0"
                    value={versionName}
                    onChange={(e) => setVersionName(e.target.value)}
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:outline-none focus:border-primary"
                  />
                </div>
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">APK Download URL</label>
                <input
                  type="url"
                  required
                  placeholder="https://cdn.example.com/builds/app-v2.1.0.apk"
                  value={apkUrl}
                  onChange={(e) => setApkUrl(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">
                  SHA-256 Checksum <span className="text-muted-foreground">(64-char hex)</span>
                </label>
                <input
                  type="text"
                  placeholder="e.g. 4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
                  value={sha256}
                  onChange={(e) => setSha256(e.target.value)}
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 font-mono text-xs text-foreground focus:outline-none focus:border-primary"
                />
              </div>

              <div>
                <label className="text-xs font-medium text-foreground">Initial Target Rollout Ring</label>
                <div className="mt-2 grid grid-cols-3 gap-2">
                  <button
                    type="button"
                    onClick={() => setInitialRing('draft')}
                    className={`rounded-xl border p-2 text-xs font-medium transition ${
                      initialRing === 'draft'
                        ? 'border-primary bg-primary/10 text-foreground'
                        : 'border-border bg-card text-muted-foreground'
                    }`}
                  >
                    Draft
                  </button>
                  <button
                    type="button"
                    onClick={() => setInitialRing('canary')}
                    className={`rounded-xl border p-2 text-xs font-medium transition ${
                      initialRing === 'canary'
                        ? 'border-primary bg-primary/10 text-primary font-semibold'
                        : 'border-border bg-card text-muted-foreground'
                    }`}
                  >
                    Canary (Recommended)
                  </button>
                  <button
                    type="button"
                    onClick={() => setInitialRing('released')}
                    className={`rounded-xl border p-2 text-xs font-medium transition ${
                      initialRing === 'released'
                        ? 'border-emerald-500 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 font-semibold'
                        : 'border-border bg-card text-muted-foreground'
                    }`}
                  >
                    Released (Fleet)
                  </button>
                </div>
                <p className="mt-1.5 text-[11px] text-muted-foreground">
                  Defaulting to <strong className="text-foreground">Canary</strong> allows testing on pilot screens before broad deployment.
                </p>
              </div>

              <div className="flex justify-end gap-3 pt-3">
                <button
                  type="button"
                  onClick={() => setPublishOpen(false)}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    !versionCode ||
                    !versionName ||
                    !apkUrl ||
                    (!isShaValid && sha256.length > 0) ||
                    createMutation.isPending
                  }
                  className="rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {createMutation.isPending ? 'Publishing…' : 'Publish Build'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
