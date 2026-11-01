'use client'

import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Package, PackagePlus, Rocket } from 'lucide-react'
import { toast } from 'sonner'
import { api } from '@/lib/api'
import type { RolloutState } from '@/lib/types'
import { relativeTime } from '@/lib/format'

export default function AdminReleasesPage() {
  const queryClient = useQueryClient()
  const [publishOpen, setPublishOpen] = useState(false)
  const [versionCode, setVersionCode] = useState('')
  const [versionName, setVersionName] = useState('')
  const [apkUrl, setApkUrl] = useState('')
  const [sha256, setSha256] = useState('')

  const releasesQuery = useQuery({ queryKey: ['admin', 'releases'], queryFn: api.getReleases })

  const createMutation = useMutation({
    mutationFn: api.createRelease,
    onSuccess: () => {
      toast.success('Release published fleet-wide')
      queryClient.invalidateQueries({ queryKey: ['admin', 'releases'] })
      setPublishOpen(false)
      setVersionCode('')
      setVersionName('')
      setApkUrl('')
      setSha256('')
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to publish release'),
  })

  const promoteMutation = useMutation({
    mutationFn: ({ versionCode, rolloutState }: { versionCode: number; rolloutState: RolloutState }) =>
      api.promoteRelease(versionCode, rolloutState),
    onSuccess: (release) => {
      toast.success(
        release.rollout_state === 'released'
          ? `v${release.version_code} is now live across all fleets`
          : `v${release.version_code} moved to ${release.rollout_state}`,
      )
      queryClient.invalidateQueries({ queryKey: ['admin', 'releases'] })
    },
    onError: (error: Error) => toast.error(error.message || 'Failed to change the rollout ring'),
  })

  const releases = releasesQuery.data || []
  const latest = releases.reduce<number | null>(
    (max, release) => (release.rollout_state === 'released' ? Math.max(max ?? 0, release.version_code) : max),
    null,
  )

  const isShaValid = /^[0-9a-fA-F]{64}$/.test(sha256.trim())

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold uppercase tracking-wider text-foreground">Platform Deployment</span>
            <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent text-foreground border border-border">
              Super Admin
            </span>
          </div>
          <h1 className="text-2xl font-bold text-foreground mt-1">App Releases & Fleet Rollout</h1>
          <p className="text-sm text-muted-foreground mt-1">
            Publish official Android TV player APK builds and manage fleet-wide rollout rings.
          </p>
        </div>

        <button
          onClick={() => setPublishOpen(!publishOpen)}
          className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-primary hover:bg-primary/90 text-primary-foreground text-sm font-semibold shadow-lg  transition-all cursor-pointer"
        >
          <PackagePlus className="size-4" />
          {publishOpen ? 'Close Form' : 'Publish New Release'}
        </button>
      </div>

      {/* Publish Form Modal/Card */}
      {publishOpen && (
        <div className="p-6 rounded-2xl bg-muted border border-border shadow-2xl space-y-4 animate-in fade-in slide-in-from-top-4 duration-200">
          <div className="flex items-center gap-2 text-foreground font-semibold">
            <Rocket className="size-5 text-foreground" />
            <span>Publish Android Player Binary</span>
          </div>
          <p className="text-xs text-muted-foreground">
            Players check for updates on their sync cycle and install automatically if running in Device Owner mode.
          </p>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMutation.mutate({
                version_code: parseInt(versionCode, 10),
                version_name: versionName.trim(),
                apk_url: apkUrl.trim(),
                sha256: sha256.trim().toLowerCase(),
                mandatory: false,
              })
            }}
            className="space-y-4"
          >
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">Version Code (integer)</label>
                <input
                  type="number"
                  min={1}
                  required
                  placeholder="e.g. 10"
                  value={versionCode}
                  onChange={(e) => setVersionCode(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-card border border-border text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-ring"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">Version Name</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. 1.5.0"
                  value={versionName}
                  onChange={(e) => setVersionName(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-card border border-border text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-ring"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">APK Download URL</label>
              <input
                type="url"
                required
                placeholder="https://storage.example.com/apks/mr-player-v1.5.0.apk"
                value={apkUrl}
                onChange={(e) => setApkUrl(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-card border border-border text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-ring"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">
                SHA256 Checksum <span className="text-muted-foreground">(64-char hex digest)</span>
              </label>
              <input
                type="text"
                placeholder="a1b2c3d4e5f6..."
                value={sha256}
                onChange={(e) => setSha256(e.target.value)}
                className="w-full font-mono text-xs px-3.5 py-2.5 rounded-xl bg-card border border-border text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-ring"
              />
            </div>

            <div className="flex justify-end gap-3 pt-2">
              <button
                type="button"
                onClick={() => setPublishOpen(false)}
                className="px-4 py-2 rounded-xl text-muted-foreground hover:text-foreground text-sm font-medium transition cursor-pointer"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!versionCode || !versionName || !apkUrl || (!isShaValid && sha256.length > 0) || createMutation.isPending}
                className="px-5 py-2.5 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-semibold shadow-lg transition cursor-pointer"
              >
                {createMutation.isPending ? 'Publishing...' : 'Confirm & Publish'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Releases Table */}
      <div className="rounded-2xl bg-card border border-border overflow-hidden">
        <div className="p-5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="size-8 rounded-lg bg-accent flex items-center justify-center text-foreground">
              <Package className="size-4" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-foreground">Published Release History</h2>
              <p className="text-xs text-muted-foreground">Manage rollout stages for all deployed binaries</p>
            </div>
          </div>
          <span className="text-xs text-muted-foreground font-medium">{releases.length} releases</span>
        </div>

        {releasesQuery.isLoading ? (
          <div className="p-8 text-center text-muted-foreground text-sm">Loading releases...</div>
        ) : releases.length === 0 ? (
          <div className="p-12 text-center space-y-3">
            <Package className="size-10 text-muted-foreground mx-auto" />
            <p className="text-sm font-medium text-muted-foreground">No releases published yet</p>
            <p className="text-xs text-muted-foreground max-w-sm mx-auto">
              Publish an Android TV APK build above to enable automated remote updates across all screens.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm text-foreground">
              <thead className="text-xs text-muted-foreground uppercase bg-muted border-b border-border">
                <tr>
                  <th className="px-5 py-3.5 font-semibold">Version Code</th>
                  <th className="px-5 py-3.5 font-semibold">Version Name</th>
                  <th className="px-5 py-3.5 font-semibold">Rollout Ring</th>
                  <th className="px-5 py-3.5 font-semibold">SHA256</th>
                  <th className="px-5 py-3.5 font-semibold">APK URL</th>
                  <th className="px-5 py-3.5 font-semibold">Published</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {releases.map((rel) => {
                  const isLatest = rel.version_code === latest
                  return (
                    <tr key={rel.id} className="hover:bg-muted transition-colors">
                      <td className="px-5 py-4 font-mono font-bold text-foreground flex items-center gap-2">
                        <span>v{rel.version_code}</span>
                        {isLatest && (
                          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-500/10 text-emerald-700 dark:text-emerald-400 border border-emerald-500/20">
                            Active Fleet Default
                          </span>
                        )}
                      </td>
                      <td className="px-5 py-4 text-foreground font-medium">{rel.version_name}</td>
                      <td className="px-5 py-4">
                        <select
                          value={rel.rollout_state}
                          onChange={(e) =>
                            promoteMutation.mutate({
                              versionCode: rel.version_code,
                              rolloutState: e.target.value as RolloutState,
                            })
                          }
                          disabled={promoteMutation.isPending}
                          className="px-3 py-1.5 rounded-lg bg-muted border border-border text-xs font-semibold text-foreground focus:outline-none focus:border-ring cursor-pointer"
                        >
                          <option value="draft">Draft (Testing only)</option>
                          <option value="canary">Canary (Pilots only)</option>
                          <option value="released">Released (Fleet Default)</option>
                        </select>
                      </td>
                      <td className="px-5 py-4 font-mono text-[11px] text-muted-foreground truncate max-w-[140px]" title={rel.sha256 || 'None'}>
                        {rel.sha256 ? `${rel.sha256.slice(0, 12)}…` : '—'}
                      </td>
                      <td className="px-5 py-4 text-xs text-foreground truncate max-w-[200px]" title={rel.apk_url}>
                        <a href={rel.apk_url} target="_blank" rel="noreferrer" className="hover:underline">
                          {rel.apk_url}
                        </a>
                      </td>
                      <td className="px-5 py-4 text-xs text-muted-foreground whitespace-nowrap">
                        {relativeTime(rel.created_at)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
