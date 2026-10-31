'use client'

import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Building2, Mail, Phone, Tag, X } from 'lucide-react'
import { api } from '@/lib/api'
import { invalidateBookingViews } from '@/lib/query-keys'
import type { Client, ContentItem } from '@/lib/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface EditClientAdModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  contentItem: ContentItem | null
  /** Optional backwards-compatibility prop */
  defaultScreenIds?: number[]
}

export function EditClientAdModal({ open, onOpenChange, contentItem }: EditClientAdModalProps) {
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [clientName, setClientName] = useState('')
  const [clientEmail, setClientEmail] = useState('')
  const [clientPhone, setClientPhone] = useState('')
  const [notes, setNotes] = useState('')
  const [showClientSuggestions, setShowClientSuggestions] = useState(false)

  // Load available clients for auto-complete
  const { data: clients = [] } = useQuery<Client[]>({
    queryKey: ['clients'],
    queryFn: () => api.getClients(),
    enabled: open,
  })

  // Pre-fill from the advert once per opening, during render rather than in an effect --
  // the same reasoning as create-booking-modal. Keyed on the advert's id so opening it on
  // a different ad re-reads that one, and never re-run while open, so a refetch landing
  // mid-edit cannot wipe a half-typed correction.
  const [seededFor, setSeededFor] = useState<number | null>(null)
  if (!open && seededFor !== null) {
    setSeededFor(null)
  }
  if (open && contentItem && seededFor !== contentItem.id) {
    setSeededFor(contentItem.id)
    setName(contentItem.name || '')
    setClientName(contentItem.client_name || '')
    setClientEmail(contentItem.client_email || '')
    setClientPhone(contentItem.client_phone || '')
    setNotes(contentItem.placement_notes || '')
  }

  // Filter client suggestions
  const filteredClients = useMemo(() => {
    if (!clientName.trim()) return clients.slice(0, 5)
    return clients.filter((c) => c.name.toLowerCase().includes(clientName.toLowerCase())).slice(0, 5)
  }, [clients, clientName])

  // Mutation to update client and ad details
  const updateMutation = useMutation({
    mutationFn: async () => {
      if (!contentItem) return
      return api.updateContentClientAd(contentItem.id, {
        name: name.trim() || undefined,
        client_name: clientName.trim(),
        client_email: clientEmail.trim() || undefined,
        client_phone: clientPhone.trim() || undefined,
        notes: notes.trim() || undefined,
      })
    },
    onSuccess: () => {
      toast.success('Ad and client details updated successfully')
      invalidateBookingViews(queryClient)
      onOpenChange(false)
    },
    onError: (err: Error) => {
      toast.error(err.message || 'Failed to update client & ad details')
    },
  })

  /** Close the suggestion list when focus leaves the name field and the list together. */
  const closeSuggestionsOnBlur = (event: React.FocusEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setShowClientSuggestions(false)
    }
  }

  const handleSelectClient = (c: Client) => {
    setClientName(c.name)
    // Replaced outright, blanks included: keeping the previous client's phone when this one
    // has none would send it on to be saved against the wrong client.
    setClientEmail(c.email ?? '')
    setClientPhone(c.phone ?? '')
    setShowClientSuggestions(false)
  }

  // The email and phone on screen belong to the client this advert is booked to. Naming a
  // different client means they no longer apply, so they are cleared rather than quietly
  // carried across to be saved against someone else.
  const changeClientName = (next: string) => {
    const sameClient = next.trim().toLowerCase() === (contentItem?.client_name || '').trim().toLowerCase()
    setClientName(next)
    if (!sameClient) {
      setClientEmail('')
      setClientPhone('')
    }
  }

  if (!contentItem) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-lg sm:p-7">
        <DialogHeader>
          <div className="flex items-center gap-2.5">
            <div className="size-9 rounded-xl bg-primary/10 text-primary grid place-items-center">
              <Building2 className="size-5" />
            </div>
            <div>
              <DialogTitle className="text-xl">Edit Client &amp; Ad Details</DialogTitle>
              <DialogDescription className="text-xs text-muted-foreground mt-0.5">
                Update advertiser contact information and creative title.
              </DialogDescription>
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-5 py-2">
          {/* Ad Title */}
          <div className="space-y-1.5">
            <Label htmlFor="ad-name" className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Ad / Creative Title
            </Label>
            <div className="relative">
              <Tag className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
              <Input
                id="ad-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Moolans Grand Opening Offer"
                className="pl-9 text-sm"
              />
            </div>
          </div>

          {/* Section: Client & Advertiser */}
          <div className="rounded-2xl border border-primary/20 bg-primary/[0.02] p-4 sm:p-5 space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Building2 className="size-4 text-primary" />
                <span className="text-xs font-bold uppercase tracking-wider text-primary">Client &amp; Advertiser</span>
              </div>
              <Badge variant="outline" className="text-[10px] font-medium border-primary/30 text-primary">
                Contact Info
              </Badge>
            </div>

            <div className="space-y-3">
              {/* Client Name with Auto-Complete */}
              <div className="space-y-1.5 relative" onBlur={closeSuggestionsOnBlur}>
                <Label htmlFor="client-name" className="text-xs font-medium">
                  Client / Brand Name <span className="text-rose-500">*</span>
                </Label>
                <div className="relative">
                  <Input
                    id="client-name"
                    value={clientName}
                    onChange={(e) => {
                      changeClientName(e.target.value)
                      setShowClientSuggestions(true)
                    }}
                    onFocus={() => setShowClientSuggestions(true)}
                    placeholder="Type or select client name..."
                    className="text-sm bg-background"
                  />
                  {clientName && (
                    <button
                      type="button"
                      onClick={() => {
                        changeClientName('')
                        setShowClientSuggestions(false)
                      }}
                      className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                    >
                      <X className="size-4" />
                    </button>
                  )}
                </div>

                {/* Dropdown Suggestions */}
                {showClientSuggestions && filteredClients.length > 0 && (
                  <div className="absolute z-50 left-0 right-0 top-full mt-1 rounded-xl border border-border bg-popover shadow-xl overflow-hidden divide-y divide-border/40">
                    <div className="px-3 py-1.5 bg-muted/40 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                      Existing Clients
                    </div>
                    {filteredClients.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => handleSelectClient(c)}
                        className="w-full text-left px-3 py-2 text-xs hover:bg-primary/10 flex items-center justify-between transition-colors"
                      >
                        <span className="font-semibold text-foreground">{c.name}</span>
                        <span className="text-[11px] text-muted-foreground">{c.email || c.phone || c.client_code}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Email & Phone */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="client-email" className="text-xs font-medium">
                    Client Email
                  </Label>
                  <div className="relative">
                    <Mail className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                    <Input
                      id="client-email"
                      type="email"
                      value={clientEmail}
                      onChange={(e) => setClientEmail(e.target.value)}
                      placeholder="client@brand.com"
                      className="pl-9 text-xs bg-background"
                    />
                  </div>
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="client-phone" className="text-xs font-medium">
                    Client Phone
                  </Label>
                  <div className="relative">
                    <Phone className="absolute left-3 top-1/2 -translate-y-1/2 size-3.5 text-muted-foreground" />
                    <Input
                      id="client-phone"
                      value={clientPhone}
                      onChange={(e) => setClientPhone(e.target.value)}
                      placeholder="+91 98765 43210"
                      className="pl-9 text-xs bg-background"
                    />
                  </div>
                </div>
              </div>
            </div>
          </div>

          {/* Campaign Notes */}
          <div className="space-y-1.5">
            <Label htmlFor="notes" className="text-xs font-medium text-muted-foreground">
              Campaign Notes / Reference
            </Label>
            <Input
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Special festive discount banner"
              className="text-xs"
            />
          </div>

          <p className="text-[11px] text-muted-foreground">
            Commercial plans, pricing, dates, and screen allocations are managed in the <strong>Booking &amp; billing</strong> section.
          </p>
        </div>

        <DialogFooter className="gap-2 sm:gap-0 mt-4">
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            type="button"
            disabled={!clientName.trim() || updateMutation.isPending}
            onClick={() => updateMutation.mutate()}
            className="font-semibold shadow-md"
          >
            {updateMutation.isPending ? 'Saving Changes...' : 'Save Details'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
