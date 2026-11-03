'use client'

import { useState, useEffect } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bell,
  Check,
  CheckCircle,
  Database,
  Film,
  HardDrive,
  Mail,
  RefreshCw,
  Save,
  Send,
  Settings,
  Shield,
  Sliders,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import { PageHeader } from '@/components/admin/admin-ui'
import { toast } from 'sonner'

export default function AdminSettingsPage() {
  const queryClient = useQueryClient()

  const [contactEmail, setContactEmail] = useState('')
  const [auditRetentionDays, setAuditRetentionDays] = useState(90)
  const [requireGoogleSso, setRequireGoogleSso] = useState(false)
  const [demoVideoUrl, setDemoVideoUrl] = useState('')
  const [offlineHours, setOfflineHours] = useState(24)
  const [expiringDays, setExpiringDays] = useState(7)
  const [storagePercent, setStoragePercent] = useState(90)

  // SMTP test state
  const [testEmailTo, setTestEmailTo] = useState('')
  const [testingSmtp, setTestingSmtp] = useState(false)

  // Fetch settings
  const { data: settings, isLoading } = useQuery({
    queryKey: ['admin', 'settings'],
    queryFn: adminApi.getSettings,
  })

  // Populate state on load
  useEffect(() => {
    if (settings) {
      setContactEmail(settings.contact_email || '')
      setAuditRetentionDays(settings.audit_retention_days || 90)
      setRequireGoogleSso(settings.require_super_admin_google_sso || false)
      setDemoVideoUrl(settings.demo_video_url || '')
      setOfflineHours(settings.attention_offline_screen_hours || 24)
      setExpiringDays(settings.attention_expiring_plan_days || 7)
      setStoragePercent(settings.attention_storage_percent || 90)
    }
  }, [settings])

  // Save settings mutation
  const saveMutation = useMutation({
    mutationFn: () =>
      adminApi.updateSettings({
        contact_email: contactEmail,
        audit_retention_days: auditRetentionDays,
        require_super_admin_google_sso: requireGoogleSso,
        demo_video_url: demoVideoUrl,
        attention_offline_screen_hours: offlineHours,
        attention_expiring_plan_days: expiringDays,
        attention_storage_percent: storagePercent,
      }),
    onSuccess: () => {
      toast.success('System settings saved and audited')
      queryClient.invalidateQueries({ queryKey: ['admin', 'settings'] })
    },
    onError: (err: Error) => toast.error(`Failed to save settings: ${err.message}`),
  })

  // Test SMTP
  async function handleSendTestEmail(e: React.FormEvent) {
    e.preventDefault()
    if (!testEmailTo.trim()) return
    setTestingSmtp(true)
    try {
      await adminApi.sendTestEmail(testEmailTo.trim())
      toast.success(`Test email dispatched to ${testEmailTo.trim()}`)
    } catch (err: any) {
      toast.error(`SMTP error: ${err.message || 'Delivery failed'}`)
    } finally {
      setTestingSmtp(false)
    }
  }

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="System Settings & Policy Governance"
        description="Configure platform contact identities, SMTP test verification, retention cycles, and automated attention thresholds"
      >
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending || isLoading}
          className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground shadow hover:bg-primary/90 disabled:opacity-50"
        >
          <Save className="size-4" />
          {saveMutation.isPending ? 'Saving Settings…' : 'Save Platform Settings'}
        </button>
      </PageHeader>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        {/* Support & Contact Identities */}
        <section className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div className="flex items-center gap-2 border-b border-border pb-3">
            <Mail className="size-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Platform Support & Mail Identity</h3>
          </div>

          <div>
            <label className="text-xs font-medium text-foreground">Platform Support Email</label>
            <input
              type="email"
              placeholder="support@mrsignage.com"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Address displayed on tenant invoice sign-offs and billing contact drafts.
            </p>
          </div>

          <div>
            <label className="text-xs font-medium text-foreground">Universal Demo Video URL</label>
            <input
              type="url"
              placeholder="https://storage.example.com/demo/universal-reel.mp4"
              value={demoVideoUrl}
              onChange={(e) => setDemoVideoUrl(e.target.value)}
              className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Default looping reel played on newly provisioned screens before first tenant playlist assignment.
            </p>
          </div>

          {/* Interactive SMTP Live Verification */}
          <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-3">
            <h4 className="text-xs font-semibold text-foreground">Live SMTP Delivery Test</h4>
            <p className="text-[11px] text-muted-foreground">
              Directly tests production mail transport connectivity and authenticates credentials.
            </p>
            <form onSubmit={handleSendTestEmail} className="flex gap-2">
              <input
                type="email"
                required
                placeholder="recipient@example.com"
                value={testEmailTo}
                onChange={(e) => setTestEmailTo(e.target.value)}
                className="flex-1 rounded-xl border border-border bg-card p-2 text-xs text-foreground focus:border-primary focus:outline-none"
              />
              <button
                type="submit"
                disabled={testingSmtp || !testEmailTo.trim()}
                className="flex items-center gap-1.5 rounded-xl bg-card border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
              >
                <Send className="size-3" />
                {testingSmtp ? 'Sending…' : 'Send Test'}
              </button>
            </form>
          </div>
        </section>

        {/* Security, Retention & Attention Thresholds */}
        <section className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div className="flex items-center gap-2 border-b border-border pb-3">
            <Sliders className="size-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Retention & Proactive Thresholds</h3>
          </div>

          {/* Audit Retention */}
          <div>
            <label className="text-xs font-medium text-foreground">Audit Log Retention Window (Days)</label>
            <input
              type="number"
              min={7}
              max={365}
              value={auditRetentionDays}
              onChange={(e) => setAuditRetentionDays(Number(e.target.value))}
              className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              Immutable platform audit logs older than this duration will be queued for cold-archive pruning.
            </p>
          </div>

          {/* Attention Queue Thresholds */}
          <div className="space-y-3 rounded-xl border border-border bg-muted/20 p-4">
            <div className="flex items-center gap-1.5">
              <Bell className="size-3.5 text-amber-500" />
              <h4 className="text-xs font-semibold text-foreground">Attention Queue Alert Triggers</h4>
            </div>

            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-[11px] text-muted-foreground">TV Offline (Hours)</label>
                <input
                  type="number"
                  min={1}
                  max={168}
                  value={offlineHours}
                  onChange={(e) => setOfflineHours(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-border bg-card p-2 text-xs text-foreground focus:outline-none"
                />
              </div>

              <div>
                <label className="text-[11px] text-muted-foreground">Plan Expiry (Days)</label>
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={expiringDays}
                  onChange={(e) => setExpiringDays(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-border bg-card p-2 text-xs text-foreground focus:outline-none"
                />
              </div>

              <div>
                <label className="text-[11px] text-muted-foreground">Storage Warn (%)</label>
                <input
                  type="number"
                  min={50}
                  max={99}
                  value={storagePercent}
                  onChange={(e) => setStoragePercent(Number(e.target.value))}
                  className="mt-1 w-full rounded-lg border border-border bg-card p-2 text-xs text-foreground focus:outline-none"
                />
              </div>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Defines the threshold limits before proactive items escalate into the operator inbox bell and attention queue.
            </p>
          </div>

          {/* Super Admin Google SSO */}
          <div className="flex items-center justify-between rounded-xl border border-border bg-muted/20 p-4">
            <div>
              <p className="text-xs font-semibold text-foreground">Enforce Super Admin Google SSO</p>
              <p className="text-[11px] text-muted-foreground">
                Rejects password authentication for super administrators across all login forms.
              </p>
            </div>
            <input
              type="checkbox"
              checked={requireGoogleSso}
              onChange={(e) => setRequireGoogleSso(e.target.checked)}
              className="size-4 rounded border-border"
            />
          </div>
        </section>
      </div>
    </div>
  )
}

