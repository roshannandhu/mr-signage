'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Contact, Mail, Phone, Plus, Trash2 } from 'lucide-react'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'
import { usePlanFeatures } from '@/lib/use-plan-features'
import type { Client } from '@/lib/types'

/**
 * The advertisers this workspace sells to.
 *
 * A booking used to record its buyer as a typed-in name, so the same customer spelled two
 * ways became two customers and there was nowhere to keep the address their report is sent
 * to. A client here is reusable across every booking they buy.
 */
export default function ClientsPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)
  const { hasCommercial } = usePlanFeatures()

  const clientsQuery = useQuery({ queryKey: ['clients'], queryFn: api.getClients })

  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<Client | null>(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [phone, setPhone] = useState('')
  // Up here with the other hooks, NOT below the `isError` early return further down.
  // Sitting after it, these two ran on a good render and were skipped the moment the
  // query failed -- fewer hooks than the render before, which is the one thing React
  // cannot survive: it throws "Rendered fewer hooks than expected" and takes the whole
  // page down with it. The trigger is a failing clients request, so the error state that
  // was meant to handle a flaky API was itself what crashed on one.
  const [search, setSearch] = useState('')
  const clients = useMemo(() => clientsQuery.data || [], [clientsQuery.data])
  const filteredClients = useMemo(() => {
    const term = search.trim().toLowerCase()
    if (!term) return clients
    return clients.filter((c) =>
      c.name.toLowerCase().includes(term) ||
      (c.client_code && c.client_code.toLowerCase().includes(term)) ||
      (c.email && c.email.toLowerCase().includes(term))
    )
  }, [clients, search])

  const reset = () => { setEditing(null); setName(''); setEmail(''); setPhone('') }
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ['clients'] })
    // A rename rewrites `advertiser` on every booking that names this client, so any
    // cached placement list is now stale.
    queryClient.invalidateQueries({ queryKey: ['placements'] })
  }
  const fail = (error: Error) => toast.error(error.message)

  const save = useMutation({
    mutationFn: () => {
      const payload = { name: name.trim(), email: email.trim() || null, phone: phone.trim() || null }
      return editing ? api.updateClient(editing.id, payload) : api.createClient(payload)
    },
    onSuccess: () => {
      refresh()
      toast.success(editing ? 'Client updated' : 'Client added')
      setOpen(false); reset()
    },
    onError: fail,
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteClient(id),
    // Worth spelling out: an operator deleting a client reasonably fears it deletes the
    // money too, and the API deliberately keeps the bookings.
    onSuccess: () => { refresh(); toast.success('Client removed. Their bookings are kept.') },
    onError: fail,
  })

  if (!hasCommercial) {
    return (
      <div className="space-y-6">
        <PageHeader
          eyebrow="Commercial"
          title="Advertiser Clients"
          description="Manage client contacts, billing details, and campaign histories."
        />
        <EmptyState
          icon={Contact}
          title="Client management not available on your plan"
          description="Managing advertiser clients requires a commercial plan tier with Ad Booking & Scheduling enabled. Contact your administrator or upgrade your subscription."
          action={
            <Button variant="outline" render={<Link href="/dashboard/billing" />}>
              View Billing &amp; Plans
            </Button>
          }
        />
      </div>
    )
  }

  if (clientsQuery.isError) {
    return <ErrorState message="Clients could not be loaded." onRetry={() => clientsQuery.refetch()} />
  }

  const openFor = (client: Client | null) => {
    if (client) {
      setEditing(client); setName(client.name); setEmail(client.email || ''); setPhone(client.phone || '')
    } else {
      reset()
    }
    setOpen(true)
  }

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Advertising"
        title="Clients"
        description="The advertisers you sell screen time to. Their details appear on the campaign report you share with them."
        actions={canEdit ? (
          <Dialog open={open} onOpenChange={(next) => { setOpen(next); if (!next) reset() }}>
            <DialogTrigger render={<Button onClick={() => openFor(null)} />}>
              <Plus data-icon="inline-start" /> Add client
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{editing ? 'Edit client' : 'Add client'}</DialogTitle>
                <DialogDescription>
                  A client ID is generated for you. The email is where their report is sent if you email it.
                </DialogDescription>
              </DialogHeader>
              <form onSubmit={(event) => { event.preventDefault(); save.mutate() }} className="space-y-4 pt-2">
                <div className="space-y-2">
                  <Label htmlFor="client-name">Company name</Label>
                  <Input id="client-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="BrightMart Retail Pvt. Ltd." required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-email">Email</Label>
                  <Input id="client-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="contact@brightmart.com" />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="client-phone">Phone</Label>
                  <Input id="client-phone" value={phone} onChange={(event) => setPhone(event.target.value)} placeholder="+91 98765 43210" />
                </div>
                <div className="flex justify-end gap-2 pt-2">
                  <Button type="button" variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
                  <Button type="submit" disabled={save.isPending}>{editing ? 'Save' : 'Add client'}</Button>
                </div>
              </form>
            </DialogContent>
          </Dialog>
        ) : undefined}
      />

      {clients.length > 0 && (
        <div className="max-w-sm">
          <Input
            placeholder="Search clients by name, code, or email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      )}

      {clientsQuery.isLoading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 3 }).map((_, index) => <Skeleton key={index} className="h-32" />)}
        </div>
      ) : !filteredClients.length ? (
        <EmptyState
          icon={Contact}
          title={search ? "No matching clients" : "No clients yet"}
          description={search ? "Try a different search term." : "Add the advertisers you sell to. You can then book an advert against a client and share their campaign report."}
          action={canEdit && !search ? <Button onClick={() => openFor(null)}>Add client</Button> : undefined}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filteredClients.map((client) => (
            <Card key={client.id} className="ring-hairline bg-card border-0 ring-1">
              <CardContent className="p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="text-foreground truncate font-semibold">{client.name}</h3>
                    <Badge variant="outline" className="mt-1">{client.client_code}</Badge>
                  </div>
                  {canEdit && (
                    <div className="flex shrink-0 items-center gap-1">
                      <Button size="sm" variant="outline" onClick={() => openFor(client)}>Edit</Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="text-destructive"
                        onClick={() => remove.mutate(client.id)}
                        aria-label={`Remove ${client.name}`}
                      >
                        <Trash2 />
                      </Button>
                    </div>
                  )}
                </div>
                <div className="text-muted-foreground mt-3 space-y-1.5 text-sm">
                  <p className="flex items-center gap-2">
                    <Mail className="size-3.5 shrink-0" aria-hidden="true" />
                    <span className="truncate">{client.email || 'No email'}</span>
                  </p>
                  <p className="flex items-center gap-2">
                    <Phone className="size-3.5 shrink-0" aria-hidden="true" />
                    <span className="truncate">{client.phone || 'No phone'}</span>
                  </p>
                </div>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-2 border-t border-border/50 pt-3 text-xs">
                  <Badge variant={(client.active_campaigns_count ?? 0) > 0 ? 'success' : 'outline'} className="text-[11px]">
                    {(client.active_campaigns_count ?? 0)} active campaign{(client.active_campaigns_count ?? 0) === 1 ? '' : 's'}
                  </Badge>
                  <span className="text-muted-foreground font-mono font-medium">
                    ₹{(((client.total_spent_paise ?? 0) / 100)).toLocaleString('en-IN')} billed
                  </span>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  )
}
