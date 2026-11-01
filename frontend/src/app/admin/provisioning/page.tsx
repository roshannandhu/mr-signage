'use client'

import React, { useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { QrCode, Wifi, Shield, Copy, Check, AlertCircle } from 'lucide-react'
import { api } from '@/lib/api'
import { toast } from 'sonner'

export default function AdminProvisioningPage() {
  const [wifiSsid, setWifiSsid] = useState('')
  const [wifiPassword, setWifiPassword] = useState('')
  const [wifiSecurity, setWifiSecurity] = useState('WPA')
  const [maxUses, setMaxUses] = useState(10)

  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [qrPayload, setQrPayload] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)

  async function generateQr(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    setQrPayload(null)

    try {
      const payload = await api.generateProvisioningQr({
        wifi_ssid: wifiSsid,
        wifi_password: wifiPassword,
        wifi_security_type: wifiSecurity,
        max_uses: maxUses,
      })
      setQrPayload(JSON.stringify(payload))
      toast.success('Zero-Touch provisioning QR code generated')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Could not generate the provisioning code'
      setError(msg)
      toast.error(msg)
    } finally {
      setLoading(false)
    }
  }

  const copyPayload = () => {
    if (!qrPayload) return
    navigator.clipboard.writeText(qrPayload)
    setCopied(true)
    toast.success('Payload JSON copied to clipboard')
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="p-8 max-w-6xl mx-auto space-y-8">
      {/* Header */}
      <div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold uppercase tracking-wider text-foreground">Zero-Touch Hardware</span>
          <span className="px-2 py-0.5 rounded-full text-[10px] font-bold bg-accent text-foreground border border-border">
            Super Admin
          </span>
        </div>
        <h1 className="text-2xl font-bold text-foreground mt-1">Device Owner Provisioning (QR)</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Generate an Android Enterprise Device-Owner QR code to configure MR Signage on fresh or factory-reset Android TVs.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Form Card */}
        <div className="lg:col-span-6">
          <form
            onSubmit={generateQr}
            className="p-6 rounded-2xl bg-card border border-border shadow-xl space-y-5"
          >
            <div className="flex items-center gap-2.5 pb-2 border-b border-border">
              <div className="size-8 rounded-lg bg-accent flex items-center justify-center text-foreground">
                <Wifi className="size-4" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-foreground">Target Wi-Fi & Site Config</h2>
                <p className="text-xs text-muted-foreground">Embedded into the QR code for automatic connection</p>
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">Wi-Fi Network Name (SSID)</label>
              <input
                type="text"
                required
                placeholder="e.g. Office-Guest or Store-WiFi"
                value={wifiSsid}
                onChange={(e) => setWifiSsid(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-muted border border-border text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-ring"
              />
            </div>

            <div>
              <label className="block text-xs font-medium text-foreground mb-1.5">Wi-Fi Password</label>
              <input
                type="text"
                placeholder="Network password (leave blank for open networks)"
                value={wifiPassword}
                onChange={(e) => setWifiPassword(e.target.value)}
                className="w-full px-3.5 py-2.5 rounded-xl bg-muted border border-border text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-ring"
              />
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">Security Type</label>
                <select
                  value={wifiSecurity}
                  onChange={(e) => setWifiSecurity(e.target.value)}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-muted border border-border text-foreground text-sm focus:outline-none focus:border-ring cursor-pointer"
                >
                  <option value="WPA">WPA / WPA2 / WPA3</option>
                  <option value="WEP">WEP</option>
                  <option value="NONE">None (Open Network)</option>
                </select>
              </div>
              <div>
                <label className="block text-xs font-medium text-foreground mb-1.5">Max Token Uses</label>
                <input
                  type="number"
                  min={1}
                  max={1000}
                  required
                  value={maxUses}
                  onChange={(e) => setMaxUses(parseInt(e.target.value, 10) || 1)}
                  className="w-full px-3.5 py-2.5 rounded-xl bg-muted border border-border text-foreground text-sm focus:outline-none focus:border-ring"
                />
              </div>
            </div>

            {error && (
              <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-700 dark:text-rose-300 text-xs flex items-start gap-2">
                <AlertCircle className="size-4 shrink-0 mt-0.5 text-rose-700 dark:text-rose-400" />
                <span>{error}</span>
              </div>
            )}

            <button
              type="submit"
              disabled={loading || !wifiSsid.trim()}
              className="w-full py-3 rounded-xl bg-primary hover:bg-primary/90 disabled:opacity-50 text-primary-foreground text-sm font-semibold shadow-lg  transition-all cursor-pointer flex items-center justify-center gap-2"
            >
              <QrCode className="size-4" />
              {loading ? 'Generating QR Code...' : 'Generate Device-Owner QR'}
            </button>
          </form>
        </div>

        {/* QR Code Display Card */}
        <div className="lg:col-span-6 flex flex-col justify-center">
          {qrPayload ? (
            <div className="p-6 rounded-2xl bg-card border border-border shadow-2xl space-y-6 text-center animate-in fade-in duration-200">
              <div className="inline-block p-4 bg-white rounded-2xl shadow-inner mx-auto">
                <QRCodeSVG value={qrPayload} size={240} level="M" />
              </div>

              <div className="space-y-2">
                <p className="text-sm font-semibold text-foreground">How to scan on Android TV:</p>
                <p className="text-xs text-muted-foreground leading-relaxed max-w-sm mx-auto">
                  On a fresh Android TV / tablet setup screen (Welcome screen), tap the screen 6 times in the same spot to trigger the QR code scanner camera.
                </p>
              </div>

              <div className="flex justify-center gap-3 pt-2">
                <button
                  onClick={copyPayload}
                  className="px-4 py-2 rounded-xl bg-muted border border-border hover:border-border text-xs font-semibold text-foreground hover:text-foreground transition flex items-center gap-1.5 cursor-pointer"
                >
                  {copied ? <Check className="size-3.5 text-emerald-700 dark:text-emerald-400" /> : <Copy className="size-3.5" />}
                  {copied ? 'Copied' : 'Copy JSON Payload'}
                </button>
              </div>
            </div>
          ) : (
            <div className="p-12 rounded-2xl bg-card border border-border text-center space-y-3">
              <div className="size-12 rounded-2xl bg-accent text-foreground flex items-center justify-center mx-auto">
                <Shield className="size-6" />
              </div>
              <p className="text-sm font-semibold text-foreground">Zero-Touch Automated Setup</p>
              <p className="text-xs text-muted-foreground leading-relaxed max-w-sm mx-auto">
                Enter your location Wi-Fi details on the left and click generate to create the provisioning QR code.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
