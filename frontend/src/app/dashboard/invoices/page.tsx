'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CalendarRange, FileDown, IndianRupee, Mail, Receipt, Search } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import type { PaymentMethod, Placement } from '@/lib/types'
import { bookingState, money, rupees } from '@/lib/format'

const asDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })

const METHOD_LABELS: Record<PaymentMethod, string> = {
  cash: 'Cash', upi: 'UPI', bank_transfer: 'Bank transfer',
  cheque: 'Cheque', card: 'Card', other: 'Other',
}

type Filter = 'all' | 'unpaid' | 'paid'

/**
 * What every client owes this workspace, and what they have paid.
 *
 * Deliberately NOT /dashboard/billing, which is this workspace's own subscription to
 * MR Signage. These are two different sets of books that used to bleed into each other: the
 * playback report carried the price and the paid pill, and the campaigns page totted up
 * revenue on a page called "Playback report". Delivery evidence lives there; money lives
 * here.
 */
export default function InvoicesPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)

  const placementsQuery = useQuery({ queryKey: ['all-placements'], queryFn: api.getAllPlacements })
  const placements = useMemo(() => placementsQuery.data || [], [placementsQuery.data])

  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    return placements.filter((placement) => {
      // On the balance, not the flag: a part-paid booking still owes money and belongs
      // in Unpaid, which is the list a tenant works through when chasing.
      const stillOwed = money(placement).balance
      if (filter === 'paid' && stillOwed > 0) return false
      if (filter === 'unpaid' && stillOwed === 0) return false
      if (!needle) return true
      return [placement.advertiser, placement.client?.name, placement.client?.client_code, placement.creative_name]
        .some((field) => field?.toLowerCase().includes(needle))
    })
  }, [placements, filter, search])

  // Outstanding counts what has NOT been settled, not what has been billed. A tenant
  // chasing money wants the second number, and the revenue tile on the campaigns page was
  // showing them the first.
  //
  // Per booking it is total MINUS received, not a test of the is_paid flag. Reading the
  // flag made a part payment settle the whole booking: recording ₹5,000 against a ₹12,000
  // campaign showed ₹0 outstanding in the tile while the row beside it said ₹7,000 was
  // still owed. The subtraction now happens once, on the server, and every page reads it
  // through the same helper.
  const balance = (p: Placement) => money(p).balance
  const outstanding = placements.reduce((sum, p) => sum + balance(p), 0)
  const collected = placements.reduce((sum, p) => sum + money(p).paid, 0)
  const awaiting = placements.filter((p) => balance(p) > 0).length

  const [paying, setPaying] = useState<Placement | null>(null)
  const [payAmount, setPayAmount] = useState('')
  const [payMethod, setPayMethod] = useState<PaymentMethod>('upi')
  const [payReference, setPayReference] = useState('')
  const [payDate, setPayDate] = useState('')
  const [busy, setBusy] = useState<number | null>(null)

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['all-placements'] })
  const fail = (error: Error) => toast.error(error.message)

  const openPayment = (placement: Placement) => {
    setPaying(placement)
    const last = placement.payments.at(-1)
    // THIS receipt, not the running total -- payments are a ledger and saving adds a row.
    // Prefilled with what is still owed, which is what is being handed over almost every
    // time. See the note in ad-bookings.tsx.
    setPayAmount(String(money(placement).balance / 100))
    setPayMethod(last?.method ?? 'upi')
    setPayReference('')
    setPayDate(new Date().toISOString().slice(0, 10))
  }

  const savePayment = useMutation({
    mutationFn: () => api.recordPayment(paying!.id, {
      amount_paise: Math.round(Number(payAmount || 0) * 100),
      method: payMethod,
      reference: payReference.trim() || null,
      paid_at: new Date(`${payDate}T12:00:00`).toISOString(),
    }),
    onSuccess: () => { refresh(); toast.success('Payment recorded'); setPaying(null) },
    onError: fail,
  })

  const clearPayment = useMutation({
    mutationFn: () => api.clearPayment(paying!.id),
    onSuccess: () => { refresh(); toast.success('Every receipt cleared; the booking is unpaid again'); setPaying(null) },
    onError: fail,
  })

  const download = async (placement: Placement) => {
    setBusy(placement.id)
    try {
      await api.downloadInvoice(placement.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'The invoice could not be generated.')
    } finally {
      setBusy(null)
    }
  }

  const email = async (placement: Placement) => {
    setBusy(placement.id)
    try {
      const sent = await api.emailBookingReport(placement.id, 'invoice')
      toast.success(`Invoice emailed to ${sent.to}`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'The invoice could not be sent.')
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Client billing"
        title="Invoices"
        description="What each client owes for their airtime, and what they have paid. Your own MR Signage subscription is under Billing."
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <div className="ring-hairline bg-card rounded-2xl p-5 shadow-sm ring-1">
          <p className="text-muted-foreground text-xs font-semibold uppercase">Outstanding</p>
          <p className="mt-1 font-mono text-2xl font-bold tabular-nums">{rupees(outstanding)}</p>
          <p className="text-muted-foreground text-xs">Billed and not yet settled</p>
        </div>
        <div className="ring-hairline bg-card rounded-2xl p-5 shadow-sm ring-1">
          <p className="text-muted-foreground text-xs font-semibold uppercase">Collected</p>
          <p className="mt-1 font-mono text-2xl font-bold tabular-nums">{rupees(collected)}</p>
          <p className="text-muted-foreground text-xs">Recorded against a payment</p>
        </div>
        <div className="ring-hairline bg-card rounded-2xl p-5 shadow-sm ring-1">
          <p className="text-muted-foreground text-xs font-semibold uppercase">Bookings</p>
          <p className="mt-1 font-mono text-2xl font-bold tabular-nums">{placements.length}</p>
          <p className="text-muted-foreground text-xs">
            {awaiting} awaiting payment
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <div className="bg-muted flex gap-1 rounded-xl p-1">
          {(['all', 'unpaid', 'paid'] as Filter[]).map((option) => (
            <Button
              key={option}
              size="sm"
              variant={filter === option ? 'default' : 'ghost'}
              className="capitalize"
              onClick={() => setFilter(option)}
            >
              {option}
            </Button>
          ))}
        </div>
        <div className="relative min-w-56 flex-1">
          <Search className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" aria-hidden="true" />
          <Input
            className="pl-9"
            placeholder="Search client, code or creative"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search invoices"
          />
        </div>
      </div>

      {placementsQuery.isPending && <Skeleton className="h-64 w-full" />}
      {placementsQuery.isError && (
        <ErrorState message="The invoices could not be loaded." onRetry={() => placementsQuery.refetch()} />
      )}

      {placementsQuery.isSuccess && !visible.length && (
        <EmptyState
          icon={Receipt}
          title={placements.length ? 'Nothing matches that' : 'No bookings sold yet'}
          description={
            placements.length
              ? 'Try a different search, or clear the filter.'
              : 'Sell an advert to a client and its invoice will appear here.'
          }
        />
      )}

      {visible.map((placement) => {
        const state = bookingState(placement)
        const bill = money(placement)
        const total = bill.total
        return (
          <Card key={placement.id} className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="flex flex-wrap items-start justify-between gap-4 p-5">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="text-foreground font-semibold">{placement.advertiser}</h3>
                  <Badge variant={state.tone}>{state.label}</Badge>
                  {placement.client && <Badge variant="outline">{placement.client.client_code}</Badge>}
                  {/* Part paid is its own state. "Paid" over an outstanding balance is the
                      kind of contradiction a client rings up about. */}
                  {bill.status === 'part_paid' ? (
                    <Badge variant="warning">
                      Part paid · {rupees(bill.paid)} in, {rupees(bill.balance)} outstanding
                    </Badge>
                  ) : (
                    <Badge variant={bill.status === 'paid' ? 'success' : 'warning'}>
                      {bill.status === 'paid' ? 'Paid' : 'Unpaid'}
                    </Badge>
                  )}
                  {/* The method of the last receipt. With several, the count is the more
                      useful thing -- "3 receipts" tells an operator to open it. */}
                  {placement.payments.length === 1 ? (
                    <Badge variant="outline">{METHOD_LABELS[placement.payments[0].method]}</Badge>
                  ) : placement.payments.length > 1 ? (
                    <Badge variant="outline">{placement.payments.length} receipts</Badge>
                  ) : null}
                </div>
                <p className="text-muted-foreground mt-1 flex flex-wrap items-center gap-1.5 text-sm">
                  <CalendarRange className="size-3.5" aria-hidden="true" />
                  {asDate(placement.starts_at)} → {asDate(placement.effective_ends_at || placement.ends_at)}
                  {placement.plan && <span>· {placement.plan.name}</span>}
                  {placement.creative_name && (
                    <Link href={`/dashboard/content/${placement.content_id}`} className="text-primary hover:underline">
                      · {placement.creative_name}
                    </Link>
                  )}
                </p>
                {/* The most recent receipt. The full ledger is in the payment dialog;
                    what belongs on a row being scanned is the last thing that happened. */}
                {placement.payments.length > 0 && (() => {
                  const last = placement.payments[placement.payments.length - 1]
                  return (
                    <p className="text-muted-foreground mt-1 text-xs">
                      {placement.payments.length > 1 && 'Latest: '}
                      {rupees(last.amount_paise)} received {asDate(last.paid_at)}
                      {last.reference && ` · ref ${last.reference}`}
                      {last.recorded_by && ` · recorded by ${last.recorded_by}`}
                    </p>
                  )
                })()}
              </div>

              <div className="flex shrink-0 items-center gap-3">
                <p className="font-mono text-lg font-semibold tabular-nums">{rupees(total)}</p>
                <Button size="sm" variant="outline" disabled={busy === placement.id} onClick={() => download(placement)}>
                  <FileDown data-icon="inline-start" /> Invoice
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy === placement.id || !placement.client?.email}
                  title={placement.client?.email ? 'Email this invoice to the client' : 'This client has no email address on file'}
                  onClick={() => email(placement)}
                >
                  <Mail data-icon="inline-start" /> Email
                </Button>
                {canEdit && (
                  <Button
                    size="sm"
                    variant={bill.balance > 0 ? 'default' : 'outline'}
                    onClick={() => openPayment(placement)}
                  >
                    <IndianRupee data-icon="inline-start" />
                    {bill.status === 'paid' ? 'Payment' : bill.status === 'part_paid' ? 'Add payment' : 'Record payment'}
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        )
      })}

      <Dialog open={Boolean(paying)} onOpenChange={(open) => { if (!open) setPaying(null) }}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Payment from {paying?.advertiser}</DialogTitle>
            <DialogDescription>
              What the client has handed over this time. Each payment is kept as its own
              receipt, so a deposit and a balance both stay on the record.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            {paying && (() => {
              const bill = money(paying)
              // Against the BALANCE, not the contract: this instalment lands on top of
              // what has already been received rather than replacing it.
              const after = bill.balance - Math.round(Number(payAmount || 0) * 100)
              return (
                <div className="bg-muted/40 ring-hairline space-y-2 rounded-xl p-3 text-sm ring-1">
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Contract value</span>
                    <span className="text-foreground font-semibold tabular-nums">{rupees(bill.total)}</span>
                  </div>
                  <div className="flex justify-between gap-3">
                    <span className="text-muted-foreground">Recorded so far</span>
                    <span className="text-foreground font-semibold tabular-nums">{rupees(bill.paid)}</span>
                  </div>
                  <div className="border-hairline flex justify-between gap-3 border-t pt-2">
                    <span className="text-muted-foreground">Balance after saving</span>
                    <span className={`font-semibold tabular-nums ${after > 0 ? 'text-amber-600 dark:text-amber-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
                      {after > 0 ? rupees(after) : 'Nothing owing'}
                    </span>
                  </div>
                </div>
              )
            })()}
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Label htmlFor="inv-amount">Amount received now (₹)</Label>
                {paying && money(paying).balance > 0 && (
                  <Button size="xs" variant="ghost"
                          onClick={() => setPayAmount(String(money(paying).balance / 100))}>
                    Settles the balance
                  </Button>
                )}
              </div>
              <Input id="inv-amount" type="number" min={0} value={payAmount}
                     onChange={(event) => setPayAmount(event.target.value)} autoFocus />
              <p className="text-muted-foreground text-xs">
                Part payments are fine — this is added to what has already been received,
                and the booking stays part paid until the two together cover the contract.
              </p>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="inv-method">Method</Label>
                <select
                  id="inv-method"
                  className="border-input bg-background h-10 w-full rounded-lg border px-3 text-sm"
                  value={payMethod}
                  onChange={(event) => setPayMethod(event.target.value as PaymentMethod)}
                >
                  {(Object.keys(METHOD_LABELS) as PaymentMethod[]).map((method) => (
                    <option key={method} value={method}>{METHOD_LABELS[method]}</option>
                  ))}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="inv-date">Received on</Label>
                <Input id="inv-date" type="date" value={payDate}
                       onChange={(event) => setPayDate(event.target.value)} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="inv-ref">Reference</Label>
              <Input id="inv-ref" value={payReference} placeholder="UTR, cheque number, transaction id"
                     onChange={(event) => setPayReference(event.target.value)} />
            </div>
          </div>

          <DialogFooter showCloseButton>
            {/* Everything off at once. The per-receipt remove lives on the ad's own page,
                where the full ledger is shown; this list is for chasing, not editing. */}
            {(paying?.payments.length || paying?.is_paid) ? (
              <Button variant="ghost" className="text-destructive" disabled={clearPayment.isPending}
                      onClick={() => clearPayment.mutate()}>
                Clear all
              </Button>
            ) : null}
            <Button
              disabled={savePayment.isPending || !(Number(payAmount) > 0)}
              onClick={() => savePayment.mutate()}
            >
              {savePayment.isPending ? 'Saving…' : 'Save payment'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
