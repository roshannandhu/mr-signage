'use client'

import React, { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  Building2,
  Check,
  CheckCircle2,
  Copy,
  Eye,
  EyeOff,
  Printer,
  QrCode,
  Shield,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import { toast } from 'sonner'
import { PageHeader } from '@/components/admin/admin-ui'

export default function AdminProvisioningPage() {
  const [selectedOrgId, setSelectedOrgId] = useState<string>('')
  const [wifiSsid, setWifiSsid] = useState('')
  const [wifiPassword, setWifiPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [wifiSecurity, setWifiSecurity] = useState('WPA')

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [qrResult, setQrResult] = useState<{
    qr_base64: string
    token: string
    expires_at: string | null
    instructions: string
  } | null>(null)
  const [copiedToken, setCopiedToken] = useState(false)

  // Fetch tenants for tenant selector
  const { data: tenants = [] } = useQuery({
    queryKey: ['admin', 'tenants', 'all-for-provisioning'],
    queryFn: () => adminApi.listTenants(),
  })
  const selectedTenant = tenants.find((t) => String(t.id) === selectedOrgId)

  async function generateQr(e: React.FormEvent) {
    e.preventDefault()
    if (!selectedOrgId) {
      setError('Please select a target tenant workspace')
      return
    }

    setLoading(true)
    setError(null)
    setQrResult(null)

    try {
      const res = await adminApi.generateTenantProvisioningQr(Number(selectedOrgId), {
        wifi_ssid: wifiSsid.trim(),
        wifi_password: wifiPassword,
        wifi_security: wifiSecurity,
      })
      setQrResult(res)
      toast.success(`Zero-Touch provisioning QR created for ${selectedTenant?.name || 'tenant'}`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not generate the provisioning code'
      setError(msg)
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  const copyToken = () => {
    if (!qrResult) return
    navigator.clipboard.writeText(qrResult.token)
    setCopiedToken(true)
    toast.success('Enrollment token copied')
    setTimeout(() => setCopiedToken(false), 2000)
  }

  const handlePrint = () => {
    window.print()
  }

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <div className="print:hidden">
        <PageHeader
          title="Zero-Touch Setup Studio"
          description="Generate Android Enterprise Device-Owner QR codes bound directly to specific tenant workspaces"
        >
          {qrResult && (
            <button
              onClick={handlePrint}
              className="flex items-center gap-2 rounded-xl border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground shadow-sm hover:bg-muted"
            >
              <Printer className="size-4" />
              Print Field Handover Sheet
            </button>
          )}
        </PageHeader>
      </div>

      {/* Setup Lifecycle Visual Guide */}
      <section className="rounded-2xl border border-border bg-card p-5 print:hidden">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          Zero-Touch Device Enrollment Sequence
        </h3>
        <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-5">
          <div className="rounded-xl border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2 text-primary font-semibold text-xs">
              <span className="flex size-5 items-center justify-center rounded-full bg-primary/20 text-[10px]">1</span>
              Enrolled
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Factory-reset TV scans QR, configures Wi-Fi credentials automatically.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2 text-primary font-semibold text-xs">
              <span className="flex size-5 items-center justify-center rounded-full bg-primary/20 text-[10px]">2</span>
              Registered
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Downloads player APK and grants Android Device Owner kiosk privileges.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2 text-primary font-semibold text-xs">
              <span className="flex size-5 items-center justify-center rounded-full bg-primary/20 text-[10px]">3</span>
              Paired
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Exchanges enrollment token for hardware cryptographic device secret.
            </p>
          </div>

          <div className="rounded-xl border border-border bg-muted/20 p-3">
            <div className="flex items-center gap-2 text-primary font-semibold text-xs">
              <span className="flex size-5 items-center justify-center rounded-full bg-primary/20 text-[10px]">4</span>
              First Sync
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Pulls assigned playlists, screen schedules, and pre-caches media assets.
            </p>
          </div>

          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3">
            <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-400 font-semibold text-xs">
              <CheckCircle2 className="size-4" />
              Playing
            </div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Active full-kiosk playback running with live health heartbeats.
            </p>
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Form Card */}
        <div className="lg:col-span-5 print:hidden">
          <form
            onSubmit={generateQr}
            className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm"
          >
            <div className="flex items-center gap-2 border-b border-border pb-3">
              <Building2 className="size-4 text-primary" />
              <h3 className="text-sm font-semibold text-foreground">Target Workspace & Network</h3>
            </div>

            {/* Target Tenant Selector */}
            <div>
              <label className="text-xs font-medium text-foreground">
                Target Tenant Workspace <span className="text-rose-500">*</span>
              </label>
              <select
                required
                value={selectedOrgId}
                onChange={(e) => setSelectedOrgId(e.target.value)}
                className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
              >
                <option value="">Select tenant to bind new screens…</option>
                {tenants.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name} (Plan: {t.plan_name || 'Free'})
                  </option>
                ))}
              </select>
              <p className="mt-1 text-[11px] text-muted-foreground">
                Screens provisioned with this QR will automatically belong to this tenant.
              </p>
            </div>

            {/* Wi-Fi SSID */}
            <div>
              <label className="text-xs font-medium text-foreground">
                Wi-Fi Network Name (SSID) <span className="text-rose-500">*</span>
              </label>
              <input
                type="text"
                required
                placeholder="e.g. Corporate-Display or Store-WiFi"
                value={wifiSsid}
                onChange={(e) => setWifiSsid(e.target.value)}
                className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
              />
            </div>

            {/* Wi-Fi Password with Show/Hide */}
            <div>
              <label className="text-xs font-medium text-foreground">Wi-Fi Password</label>
              <div className="relative mt-1">
                <input
                  type={showPassword ? 'text' : 'password'}
                  placeholder="Network password (leave blank if open)"
                  value={wifiPassword}
                  onChange={(e) => setWifiPassword(e.target.value)}
                  className="w-full rounded-xl border border-border bg-muted/40 p-2.5 pr-10 text-xs text-foreground focus:border-primary focus:outline-none"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showPassword ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
                </button>
              </div>
            </div>

            {/* Wi-Fi Security */}
            <div>
              <label className="text-xs font-medium text-foreground">Security Protocol</label>
              <select
                value={wifiSecurity}
                onChange={(e) => setWifiSecurity(e.target.value)}
                className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground focus:border-primary focus:outline-none"
              >
                <option value="WPA">WPA / WPA2 / WPA3 (Standard)</option>
                <option value="WEP">WEP (Legacy)</option>
                <option value="NONE">None (Open Network)</option>
              </select>
            </div>

            {error && (
              <div className="flex items-start gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 p-3 text-xs text-rose-700 dark:text-rose-400">
                <AlertCircle className="mt-0.5 size-4 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !selectedOrgId || !wifiSsid.trim()}
              className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-2.5 text-xs font-semibold text-primary-foreground shadow hover:bg-primary/90 disabled:opacity-50"
            >
              <QrCode className="size-4" />
              {loading ? 'Generating Provisioning QR…' : 'Generate Device-Owner QR'}
            </button>
          </form>
        </div>

        {/* QR Display & Printable Sheet Card */}
        <div className="lg:col-span-7">
          {qrResult ? (
            <div className="space-y-6 rounded-2xl border border-border bg-card p-6 shadow-sm print:border-none print:shadow-none print:p-0">
              <div className="border-b border-border pb-4 print:border-black">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-base font-bold text-foreground print:text-black">
                      MR Signage — Hardware Provisioning Sheet
                    </h3>
                    <p className="text-xs text-muted-foreground print:text-gray-700">
                      Tenant Workspace:{' '}
                      <strong className="text-foreground print:text-black">{selectedTenant?.name || 'Selected Tenant'}</strong>
                    </p>
                  </div>
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-0.5 text-xs font-medium text-emerald-700 dark:text-emerald-400 print:text-black print:border-black">
                    Zero-Touch Ready
                  </span>
                </div>
              </div>

              <div className="flex flex-col items-center justify-center space-y-4 py-4">
                <div className="rounded-2xl border-4 border-card bg-white p-4 shadow-md print:border-2 print:border-black">
                  <img
                    src={`data:image/png;base64,${qrResult.qr_base64}`}
                    alt="Android Enterprise Provisioning QR Code"
                    className="size-64 object-contain"
                  />
                </div>

                <div className="max-w-md text-center">
                  <p className="text-xs font-semibold text-foreground print:text-black">
                    Onboarding Instruction for Field Technicians:
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground leading-relaxed print:text-gray-800">
                    {qrResult.instructions}
                  </p>
                </div>
              </div>

              <div className="space-y-2 rounded-xl border border-border bg-muted/20 p-4 text-xs print:border-black print:bg-transparent">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground print:text-gray-600">Target Wi-Fi SSID:</span>
                  <span className="font-semibold text-foreground print:text-black">{wifiSsid}</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground print:text-gray-600">Enrollment Token:</span>
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-foreground print:text-black">{qrResult.token}</span>
                    <button
                      type="button"
                      onClick={copyToken}
                      className="print:hidden rounded p-1 text-muted-foreground hover:text-foreground"
                    >
                      {copiedToken ? <Check className="size-3.5 text-emerald-500" /> : <Copy className="size-3.5" />}
                    </button>
                  </div>
                </div>
                {qrResult.expires_at && (
                  <div className="flex items-center justify-between">
                    <span className="text-muted-foreground print:text-gray-600">Token Expiration:</span>
                    <span className="text-muted-foreground print:text-black">
                      {new Date(qrResult.expires_at).toLocaleString()}
                    </span>
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex h-full min-h-[360px] flex-col items-center justify-center rounded-2xl border border-border bg-card p-8 text-center print:hidden">
              <div className="flex size-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                <Shield className="size-7" />
              </div>
              <h4 className="mt-4 text-sm font-semibold text-foreground">Awaiting Provisioning Parameters</h4>
              <p className="mt-1 max-w-sm text-xs text-muted-foreground">
                Select a tenant on the left and input the deployment location&apos;s Wi-Fi network to generate a zero-touch Android Device-Owner setup QR sheet.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
