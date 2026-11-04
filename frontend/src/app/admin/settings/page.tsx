'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Bell, Mail, Save, Send, Sliders } from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { SystemSettings } from '@/lib/types'
import { PageHeader } from '@/components/admin/admin-ui'
import { toast } from 'sonner'

export default function AdminSettingsPage() {
  const { data: settings, isLoading } = useQuery({
    queryKey: ['admin', 'settings'],
    queryFn: adminApi.getSettings,
  })

  if (isLoading || !settings) {
    return (
      <div className="space-y-6 p-6 text-foreground lg:p-8">
        <PageHeader
          title="System Settings & Policy Governance"
          description="Configure platform contact identities, SMTP test verification, retention cycles, and automated attention thresholds"
        />
        <div className="rounded-2xl border border-border bg-card p-12 text-center text-sm text-muted-foreground">
          Loading platform settings…
        </div>
      </div>
    )
  }

  return <SettingsForm initialSettings={settings} />
}

function SettingsForm({ initialSettings }: { initialSettings: SystemSettings }) {
  const queryClient = useQueryClient()

  const [contactEmail, setContactEmail] = useState(initialSettings.contact_email || '')
  const [auditRetentionDays, setAuditRetentionDays] = useState(initialSettings.audit_retention_days || 90)
  const [requireGoogleSso, setRequireGoogleSso] = useState(initialSettings.require_super_admin_google_sso || false)
  const [demoVideoUrl, setDemoVideoUrl] = useState(initialSettings.demo_video_url || '')
  const [offlineHours, setOfflineHours] = useState(initialSettings.attention_offline_screen_hours || 24)
  const [expiringDays, setExpiringDays] = useState(initialSettings.attention_expiring_plan_days || 7)
  const [storagePercent, setStoragePercent] = useState(initialSettings.attention_storage_percent || 90)

  // SMTP test state
  const [testEmailTo, setTestEmailTo] = useState('')
  const [testingSmtp, setTestingSmtp] = useState(false)

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
      queryClient.invalidateQueries({ queryKey: ['admin', 'demo-video'] })
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
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Delivery failed'
      toast.error(`SMTP error: ${message}`)
    } finally {
      setTestingSmtp(false)
    }
  }

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="System Settings & Policy Governance"
        description="Configure platform contact identities, SMTP test verification, retention cycles, and automated attention thresholds"
      >
        <button
          onClick={() => saveMutation.mutate()}
          disabled={saveMutation.isPending}
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

          <div className="space-y-1">
            <label className="text-xs font-semibold text-foreground">Primary Support Email</label>
            <input
              type="email"
              value={contactEmail}
              onChange={(e) => setContactEmail(e.target.value)}
              placeholder="support@company.com"
              className="w-full rounded-xl border border-border bg-muted/30 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <p className="text-[11px] text-muted-foreground">
              Displayed on public invoices, tenant support cards, and outgoing notification headers.
            </p>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-semibold text-foreground">Universal Demo Video Asset URL</label>
            <input
              type="text"
              value={demoVideoUrl}
              onChange={(e) => setDemoVideoUrl(e.target.value)}
              placeholder="https://assets.domain.com/demo.mp4"
              className="w-full rounded-xl border border-border bg-muted/30 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <p className="text-[11px] text-muted-foreground">
              Direct CDN or R2 link played automatically by TV screens prior to tenant workspace activation.
            </p>
          </div>

          {/* Test SMTP Email Dispatcher */}
          <div className="rounded-xl border border-border bg-muted/20 p-4 space-y-3">
            <h4 className="text-xs font-semibold text-foreground">Live SMTP Test Dispatcher</h4>
            <p className="text-[11px] text-muted-foreground">
              Verify transactional SMTP transport by dispatching an authentic diagnostic test email.
            </p>
            <form onSubmit={handleSendTestEmail} className="flex flex-col sm:flex-row gap-2">
              <input
                type="email"
                value={testEmailTo}
                onChange={(e) => setTestEmailTo(e.target.value)}
                placeholder="operator@company.com"
                className="flex-1 rounded-xl border border-border bg-card p-2 text-xs text-foreground focus:outline-none"
              />
              <button
                type="submit"
                disabled={testingSmtp || !testEmailTo.trim()}
                className="flex items-center gap-1.5 rounded-xl border border-border bg-card px-3 py-2 text-xs font-semibold text-foreground hover:bg-muted disabled:opacity-50"
              >
                <Send className="size-3" />
                {testingSmtp ? 'Sending…' : 'Send Test'}
              </button>
            </form>
          </div>
        </section>

        {/* Governance & Attention Thresholds */}
        <section className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div className="flex items-center gap-2 border-b border-border pb-3">
            <Sliders className="size-4 text-primary" />
            <h3 className="text-sm font-semibold text-foreground">Platform Governance & Operational Thresholds</h3>
          </div>

          <div className="space-y-1">
            <label className="text-xs font-semibold text-foreground">Audit Trail Retention (Days)</label>
            <select
              value={auditRetentionDays}
              onChange={(e) => setAuditRetentionDays(Number(e.target.value))}
              className="w-full rounded-xl border border-border bg-muted/30 p-2.5 text-xs text-foreground focus:outline-none"
            >
              <option value={30}>30 Days</option>
              <option value={90}>90 Days (Recommended)</option>
              <option value={180}>180 Days</option>
              <option value={365}>365 Days (1 Year Compliance)</option>
            </select>
          </div>

          {/* Attention Queue Thresholds */}
          <div className="space-y-2 rounded-xl border border-border bg-muted/20 p-4">
            <div className="flex items-center gap-2">
              <Bell className="size-3.5 text-amber-500" />
              <h4 className="text-xs font-semibold text-foreground">Proactive Queue Triggers</h4>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
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
