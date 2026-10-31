'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, IndianRupee, Mail, Receipt, Save, Send, XCircle } from 'lucide-react'
import { adminApi } from '@/lib/api'
import { Feedback, PageHeader, formatPaise } from '@/components/admin/admin-ui'
import { CustomRequestsPanel } from '@/components/admin/custom-requests-panel'

const kindLabel: Record<string, string> = {
  plan: 'Package',
  custom: 'Custom plan',
  subscription: 'Renewal',
}

/**
 * Admin Billing: what tenants have paid (view only), their custom-plan requests (moved here
 * from Packages), and the platform's own contact address and SMTP health -- the three things
 * an operator needs to run the commercial side of the platform from one place.
 */
export default function AdminBillingPage() {
  const queryClient = useQueryClient()
  const paymentsQuery = useQuery({ queryKey: ['admin', 'payments'], queryFn: () => adminApi.listPayments() })

  const totalPaise = (paymentsQuery.data ?? []).reduce((sum, p) => sum + p.amount_paise, 0)

  // --- contact email ---------------------------------------------------------------------
  const contactQuery = useQuery({ queryKey: ['admin', 'contact-email'], queryFn: adminApi.getContactEmail })
  const [contactDraft, setContactDraft] = useState<string | null>(null)
  const contactValue = contactDraft ?? contactQuery.data?.email ?? ''
  const [contactMsg, setContactMsg] = useState('')
  const [contactErr, setContactErr] = useState('')
  const saveContact = useMutation({
    mutationFn: (email: string) => adminApi.setContactEmail(email),
    onSuccess: () => {
      setContactErr('')
      setContactMsg('Saved. New custom-plan request emails will be addressed here.')
      setContactDraft(null)
      queryClient.invalidateQueries({ queryKey: ['admin', 'contact-email'] })
    },
    onError: (e: Error) => { setContactMsg(''); setContactErr(e.message) },
  })

  // --- SMTP status + test email ------------------------------------------------------------
  const statusQuery = useQuery({ queryKey: ['admin', 'email-status'], queryFn: adminApi.getPlatformEmailStatus })
  const [testTo, setTestTo] = useState('')
  const [testMsg, setTestMsg] = useState('')
  const [testErr, setTestErr] = useState('')
  const sendTest = useMutation({
    mutationFn: (to: string) => adminApi.sendTestEmail(to),
    onSuccess: (r) => { setTestErr(''); setTestMsg(`Sent to ${r.to}. Check that inbox (and spam).`) },
    onError: (e: Error) => { setTestMsg(''); setTestErr(e.message) },
  })

  return (
    <div className="space-y-8 p-6 text-foreground lg:p-8">
      <PageHeader title="Billing" description="What tenants have paid, their custom-plan requests, and the platform's own contact and email setup" />

      {/* Payments ------------------------------------------------------------------------ */}
      <section className="overflow-hidden rounded-2xl border border-border bg-card">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border p-4">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Receipt className="size-4 text-violet-400" />
            {paymentsQuery.data?.length ?? 0} payments
          </div>
          {paymentsQuery.data && paymentsQuery.data.length > 0 && (
            <p className="flex items-center gap-1 text-sm font-semibold text-emerald-400">
              <IndianRupee className="size-3.5" /> {formatPaise(totalPaise)} total
            </p>
          )}
        </div>
        {paymentsQuery.isLoading ? (
          <p className="p-10 text-center text-sm text-muted-foreground">Loading…</p>
        ) : paymentsQuery.isError ? (
          <div className="p-6">
            <Feedback error="Payments could not be loaded." />
          </div>
        ) : paymentsQuery.data?.length === 0 ? (
          <p className="p-10 text-center text-sm text-muted-foreground">No payments recorded yet. They appear here the moment a purchase is captured.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-border bg-muted text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-4 pl-5 text-left">Tenant</th>
                  <th className="p-4 text-left">Kind</th>
                  <th className="p-4 text-left">Amount</th>
                  <th className="p-4 text-left">Provider</th>
                  <th className="p-4 text-left">Order / payment id</th>
                  <th className="p-4 pr-5 text-left">Paid</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {paymentsQuery.data!.map((payment) => (
                  <tr key={payment.id} className="hover:bg-muted">
                    <td className="p-4 pl-5 font-medium">{payment.organization_name ?? `Workspace ${payment.organization_id}`}</td>
                    <td className="p-4 text-muted-foreground">{kindLabel[payment.kind] ?? payment.kind}</td>
                    <td className="p-4 font-semibold">{formatPaise(payment.amount_paise)}</td>
                    <td className="p-4 text-muted-foreground capitalize">{payment.provider}</td>
                    <td className="p-4 font-mono text-xs text-muted-foreground">
                      {payment.provider_payment_id ?? payment.provider_order_id ?? '—'}
                    </td>
                    <td className="p-4 pr-5 text-muted-foreground">
                      {new Date(payment.paid_at).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Custom / Business requests -------------------------------------------------------- */}
      <CustomRequestsPanel />

      {/* Contact email + SMTP health -------------------------------------------------------- */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <Mail className="size-4 text-violet-400" />
            Custom-plan request contact address
          </div>
          <p className="text-xs text-muted-foreground">
            Where a tenant&apos;s Custom / Business request draft is addressed once they submit it. Nothing sends automatically -- it opens their own mail app.
          </p>
          <Feedback ok={contactMsg} error={contactErr} />
          <form
            onSubmit={(e) => { e.preventDefault(); saveContact.mutate(contactValue.trim()) }}
            className="flex flex-wrap items-center gap-2"
          >
            <input
              type="email"
              value={contactValue}
              onChange={(e) => setContactDraft(e.target.value)}
              placeholder="sales@yourcompany.com"
              className="min-w-0 flex-1 rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-violet-500/50"
            />
            <button
              type="submit"
              disabled={saveContact.isPending}
              className="flex items-center gap-1.5 rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-2 text-xs text-violet-300 transition-all hover:bg-violet-500/20 disabled:opacity-50"
            >
              <Save className="size-3.5" />
              {saveContact.isPending ? 'Saving…' : 'Save'}
            </button>
          </form>
        </section>

        <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Send className="size-4 text-violet-400" />
              Email delivery
            </div>
            {statusQuery.data && (
              statusQuery.data.is_configured ? (
                <span className="flex items-center gap-1.5 rounded-full bg-emerald-500/10 px-2.5 py-1 text-xs font-medium text-emerald-300">
                  <CheckCircle2 className="size-3.5" /> Configured
                </span>
              ) : (
                <span className="flex items-center gap-1.5 rounded-full bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-300">
                  <XCircle className="size-3.5" /> Not configured
                </span>
              )
            )}
          </div>
          {statusQuery.data?.is_configured ? (
            <p className="text-xs text-muted-foreground">Sending as <span className="font-mono">{statusQuery.data.sender}</span>.</p>
          ) : (
            <p className="text-xs text-muted-foreground">
              Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD and SMTP_FROM on the server.
              {statusQuery.data?.missing_variables.length ? ` Missing: ${statusQuery.data.missing_variables.join(', ')}.` : ''}
            </p>
          )}
          <Feedback ok={testMsg} error={testErr} />
          <form
            onSubmit={(e) => { e.preventDefault(); sendTest.mutate(testTo.trim()) }}
            className="flex flex-wrap items-center gap-2"
          >
            <input
              type="email"
              required
              value={testTo}
              onChange={(e) => setTestTo(e.target.value)}
              placeholder="you@yourcompany.com"
              className="min-w-0 flex-1 rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground outline-none focus:border-violet-500/50"
            />
            <button
              type="submit"
              disabled={sendTest.isPending || !testTo.trim()}
              className="flex items-center gap-1.5 rounded-lg border border-violet-500/20 bg-violet-500/10 px-3 py-2 text-xs text-violet-300 transition-all hover:bg-violet-500/20 disabled:opacity-50"
            >
              <Send className="size-3.5" />
              {sendTest.isPending ? 'Sending…' : 'Send test email'}
            </button>
          </form>
        </section>
      </div>
    </div>
  )
}
