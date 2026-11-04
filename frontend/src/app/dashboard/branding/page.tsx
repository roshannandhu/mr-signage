'use client'

import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Image from 'next/image'
import { ImageIcon, Palette, Trash2, Upload } from 'lucide-react'
import { toast } from 'sonner'
import { ErrorState } from '@/components/dashboard/error-state'
import { PageHeader } from '@/components/dashboard/page-header'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { canEditTenantContent } from '@/lib/roles'
import { useAuthStore } from '@/lib/store'

const LOGO_TYPES = ['image/png', 'image/jpeg', 'image/webp']
const MAX_LOGO_BYTES = 2 * 1024 * 1024

/**
 * How this workspace appears on the report its clients receive.
 *
 * Workspace-level, so it lives here rather than on Profile: that page is the signed-in
 * person's own name and password, and what a client sees at the top of an invoice-shaped
 * document is not a personal setting.
 */
export default function BrandingPage() {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)
  const fileInput = useRef<HTMLInputElement>(null)

  const brandingQuery = useQuery({ queryKey: ['branding'], queryFn: api.getBranding })

  const [brandName, setBrandName] = useState('')
  const [brandColor, setBrandColor] = useState('#0b1437')

  // Seeded from the server ONCE, when it first answers.
  //
  // The intent was always this -- "typing must not be overwritten each time the query
  // refetches" -- but keying the effect on `brandingQuery.data` did the opposite of what
  // it said: React Query hands back a fresh object on every background refetch, so the
  // identity changed, the effect re-ran, and a refetch triggered by tabbing away and back
  // replaced whatever the operator was halfway through typing with the saved value.
  // A once-only flag is what "once it answers" actually needs.
  const [seeded, setSeeded] = useState(false)
  if (brandingQuery.data && !seeded) {
    setSeeded(true)
    setBrandName(brandingQuery.data.brand_name || '')
    setBrandColor(brandingQuery.data.brand_color || '#0b1437')
  }

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['branding'] })
  const fail = (error: Error) => toast.error(error.message)

  const save = useMutation({
    mutationFn: () => api.updateBranding({
      // Blank restores the workspace-name fallback rather than printing an empty masthead.
      brand_name: brandName.trim() || null,
      brand_color: brandColor || null,
    }),
    onSuccess: () => { refresh(); toast.success('Branding saved') },
    onError: fail,
  })

  const uploadLogo = useMutation({
    mutationFn: (file: File) => api.uploadBrandLogo(file),
    onSuccess: () => { refresh(); toast.success('Logo updated') },
    onError: fail,
  })

  const clearLogo = useMutation({
    mutationFn: () => api.removeBrandLogo(),
    onSuccess: () => { refresh(); toast.success('Logo removed') },
    onError: fail,
  })

  const pickLogo = (file: File | undefined) => {
    if (!file) return
    // Checked here as well as on the server so the operator is told immediately instead of
    // waiting out an upload that was always going to be refused.
    if (!LOGO_TYPES.includes(file.type)) {
      toast.error('The logo must be a PNG, JPEG or WebP image.')
      return
    }
    if (file.size > MAX_LOGO_BYTES) {
      toast.error(`That file is ${(file.size / 1024 / 1024).toFixed(1)}MB; the limit is 2MB.`)
      return
    }
    uploadLogo.mutate(file)
  }

  if (brandingQuery.isError) {
    return <ErrorState message="Branding could not be loaded." onRetry={() => brandingQuery.refetch()} />
  }

  const branding = brandingQuery.data

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Workspace"
        title="Branding"
        description="Your logo, brand name, and header accent colour across your workspace header, client campaign reports, and PDF invoices."
      />

      {brandingQuery.isLoading || !branding ? (
        <div className="grid gap-6 lg:grid-cols-2"><Skeleton className="h-72" /><Skeleton className="h-72" /></div>
      ) : (
        <div className="grid items-start gap-6 lg:grid-cols-2">
          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="space-y-5 p-6">
              <div className="space-y-2">
                <Label htmlFor="brand-name">Brand name</Label>
                <Input
                  id="brand-name"
                  value={brandName}
                  onChange={(event) => setBrandName(event.target.value)}
                  placeholder={branding.effective_name}
                  disabled={!canEdit}
                />
                <p className="text-muted-foreground text-xs">
                  Leave blank to use your workspace name. Clients currently see{' '}
                  <span className="text-foreground font-medium">{branding.effective_name}</span>.
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="brand-color">Header colour</Label>
                <div className="flex items-center gap-3">
                  <input
                    id="brand-color"
                    type="color"
                    value={brandColor}
                    onChange={(event) => setBrandColor(event.target.value)}
                    disabled={!canEdit}
                    className="border-input h-10 w-16 cursor-pointer rounded-lg border bg-transparent"
                  />
                  <Input
                    value={brandColor}
                    onChange={(event) => setBrandColor(event.target.value)}
                    disabled={!canEdit}
                    className="max-w-32 font-mono"
                    aria-label="Header colour hex value"
                  />
                </div>
              </div>

              {canEdit && (
                <Button onClick={() => save.mutate()} disabled={save.isPending}>
                  <Palette data-icon="inline-start" />
                  {save.isPending ? 'Saving…' : 'Save branding'}
                </Button>
              )}
            </CardContent>
          </Card>

          <Card className="ring-hairline bg-card border-0 ring-1">
            <CardContent className="space-y-5 p-6">
              <div className="space-y-2">
                <Label>Logo</Label>
                {/* Previewed on the actual header colour, because a white wordmark on a
                    white card looks broken and a dark one on a dark band is invisible --
                    neither is obvious until a client opens the PDF. */}
                <div
                  className="grid h-24 place-items-center rounded-xl px-4"
                  style={{ backgroundColor: brandColor }}
                >
                  {branding.logo_url ? (
                    <Image
                      src={branding.logo_url}
                      alt="Your logo, as it appears on the report"
                      width={200}
                      height={64}
                      unoptimized
                      className="max-h-16 w-auto object-contain"
                    />
                  ) : (
                    <span className="flex items-center gap-2 text-sm font-semibold text-white/80">
                      <ImageIcon className="size-4" aria-hidden="true" />
                      {branding.effective_name}
                    </span>
                  )}
                </div>
                <p className="text-muted-foreground text-xs">PNG, JPEG or WebP, up to 2MB.</p>
              </div>

              {canEdit && (
                <div className="flex flex-wrap items-center gap-2">
                  <input
                    ref={fileInput}
                    type="file"
                    accept={LOGO_TYPES.join(',')}
                    className="hidden"
                    onChange={(event) => {
                      pickLogo(event.target.files?.[0])
                      // Cleared so re-picking the same file still fires a change event.
                      event.target.value = ''
                    }}
                  />
                  <Button variant="outline" onClick={() => fileInput.current?.click()} disabled={uploadLogo.isPending}>
                    <Upload data-icon="inline-start" />
                    {uploadLogo.isPending ? 'Uploading…' : branding.logo_url ? 'Replace logo' : 'Upload logo'}
                  </Button>
                  {branding.logo_url && (
                    <Button
                      variant="ghost"
                      className="text-destructive"
                      onClick={() => clearLogo.mutate()}
                      disabled={clearLogo.isPending}
                    >
                      <Trash2 data-icon="inline-start" /> Remove
                    </Button>
                  )}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  )
}
