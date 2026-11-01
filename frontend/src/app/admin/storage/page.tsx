'use client'

import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Database, HardDrive, Layers, RefreshCw } from 'lucide-react'
import { adminApi } from '@/lib/api'
import { Feedback, PageHeader, StatCard, formatBytes } from '@/components/admin/admin-ui'

/**
 * What the object store actually holds, and which workspace put it there.
 *
 * The per-tenant quota answers "what may this workspace use". This answers "what am I being
 * billed for", which nothing did. They are not the same number and drift apart on purpose:
 * quotas are promises and get oversold, while the bucket accumulates renditions, orphans
 * from deletes that never reached storage, and objects written under prefix conventions
 * that have since changed.
 *
 * Counted by walking the bucket rather than summing the database, because the database is
 * exactly the thing being checked.
 */
export default function AdminStoragePage() {
  const queryClient = useQueryClient()
  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['admin', 'storage'],
    queryFn: () => adminApi.getStorage(),
    // Listing a bucket is charged per request and the server caches it anyway; there is no
    // reason for a dashboard left open to keep re-counting.
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: false,
  })

  const unattributed = (data?.tenants ?? []).filter((t) => t.organization_id == null)
  const orphanBytes = unattributed.reduce((sum, t) => sum + t.bucket_bytes, 0)

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="Storage"
        description="What the bucket actually holds, and which workspace it belongs to"
      >
        <button
          onClick={async () => {
            await adminApi.getStorage(true)
            queryClient.invalidateQueries({ queryKey: ['admin', 'storage'] })
          }}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition-all hover:bg-muted"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Recount
        </button>
      </PageHeader>

      <Feedback ok="" error={error ? (error as Error).message : ''} />

      {/* On `error`, not on `!configured`. A LIST that fails -- a token without list
          permission, a bucket that moved -- comes back configured:true WITH an error, and
          this banner used to ignore that case entirely: every counter read a confident
          0 B, which is indistinguishable from a bucket that is genuinely empty. */}
      {data?.error && (
        <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
          <p className="text-muted-foreground">
            {data.error}
            {data.configured && ' Until this is fixed the totals below are not a measurement.'}
          </p>
        </div>
      )}

      {/* And the other half of that ambiguity: say when the count succeeded and found
          nothing, rather than leaving four zeroes to be read as a failure. */}
      {data?.configured && !data.error && data.object_count === 0 && (
        <div className="flex items-start gap-3 rounded-xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-sky-700 dark:text-sky-400" />
          <p className="text-muted-foreground">
            The bucket was counted successfully and holds nothing. Where a workspace shows
            bytes under &ldquo;per database&rdquo;, those files are still in the Postgres
            mirror and have not been copied to object storage —{' '}
            <code className="text-foreground">scripts/sync_db_blobs_to_r2.py</code> moves them.
          </p>
        </div>
      )}

      {isLoading ? (
        <p className="p-10 text-center text-sm text-muted-foreground">Counting the bucket…</p>
      ) : data ? (
        <>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard label="Stored in bucket" value={formatBytes(data.total_bytes)} icon={HardDrive} accent="violet" />
            <StatCard label="Objects" value={data.object_count} icon={Layers} accent="cyan" />
            <StatCard label="Promised to tenants" value={formatBytes(data.committed_quota_bytes)} icon={Database} accent="emerald" />
            <StatCard
              label={data.budget_bytes ? 'Left of budget' : 'Budget'}
              value={data.budget_bytes ? formatBytes(Math.max(data.budget_bytes - data.total_bytes, 0)) : 'Not set'}
              icon={AlertTriangle}
              accent="amber"
            />
          </div>

          <p className="text-xs text-muted-foreground/70">
            {data.bucket && <><span className="text-foreground">{data.bucket}</span> · </>}
            Object storage has no fixed size — R2 and S3 both bill per GB stored with no
            ceiling — so &ldquo;how much is left&rdquo; only means something against a budget
            you choose. Set <code className="text-foreground">PLATFORM_STORAGE_BUDGET_BYTES</code>{' '}
            to track one. &ldquo;Promised to tenants&rdquo; is every workspace&apos;s quota added
            up; it is normal for that to exceed what is stored, and normal to oversell it.
          </p>

          {orphanBytes > 0 && (
            <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-700 dark:text-amber-400" />
              <p className="text-muted-foreground">
                <span className="text-foreground">{formatBytes(orphanBytes)}</span> across{' '}
                {unattributed.length} prefix{unattributed.length === 1 ? '' : 'es'} belongs to no
                workspace. You are billed for it and nothing tracks it — usually objects written
                before the current naming, or left behind by a workspace that was removed.
              </p>
            </div>
          )}

          <section className="overflow-hidden rounded-2xl border border-border bg-card">
            <div className="border-b border-border p-4 text-sm font-semibold">
              Per workspace
            </div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[820px] text-sm">
                <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="p-4 pl-5 text-left">Workspace</th>
                    <th className="p-4 text-left">Prefix</th>
                    <th className="p-4 text-right">In bucket</th>
                    <th className="p-4 text-right">Objects</th>
                    <th className="p-4 text-right">Per database</th>
                    <th className="p-4 pr-5 text-right">Quota</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {data.tenants.map((tenant) => {
                    // A gap either way is worth seeing: more in the bucket than the database
                    // knows about is an orphan being paid for, less is content whose objects
                    // never arrived.
                    const drift = tenant.bucket_bytes - tenant.database_bytes
                    const notable = Math.abs(drift) > 1024 * 1024
                    return (
                      <tr key={tenant.prefix} className="transition-colors hover:bg-muted">
                        <td className="p-4 pl-5">
                          <span className={tenant.organization_id ? 'font-medium text-foreground' : 'text-amber-700 dark:text-amber-400'}>
                            {tenant.name}
                          </span>
                        </td>
                        <td className="p-4 font-mono text-xs text-muted-foreground">{tenant.prefix}</td>
                        <td className="p-4 text-right text-foreground">{formatBytes(tenant.bucket_bytes)}</td>
                        <td className="p-4 text-right text-muted-foreground">{tenant.bucket_objects}</td>
                        <td className="p-4 text-right text-muted-foreground">
                          {formatBytes(tenant.database_bytes)}
                          {notable && (
                            <span className="ml-1.5 text-[11px] text-amber-700 dark:text-amber-400">
                              {drift > 0 ? '+' : '−'}{formatBytes(Math.abs(drift))}
                            </span>
                          )}
                        </td>
                        <td className="p-4 pr-5 text-right text-muted-foreground">
                          {tenant.quota_bytes ? formatBytes(tenant.quota_bytes) : 'No limit'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </section>
        </>
      ) : null}
    </div>
  )
}
