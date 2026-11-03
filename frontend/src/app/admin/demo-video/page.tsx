'use client'

import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, Save, UploadCloud } from 'lucide-react'
import { Feedback, PageHeader } from '@/components/admin/admin-ui'
import { adminApi } from '@/lib/api'

/**
 * The universal demo reel.
 *
 * One setting for the whole platform: any TV belonging to a workspace that has not been
 * approved yet plays this instead of sitting dark, so a prospect can see the product
 * working before their paperwork clears.
 */
export default function AdminDemoVideoPage() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [url, setUrl] = useState('')
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')

  const { data, isLoading } = useQuery({ queryKey: ['admin', 'demo-video'], queryFn: adminApi.getDemoVideo })

  // Seeded once, when the server first answers -- not on every change of the fetched URL,
  // which would overwrite an address being typed the moment a refetch landed.
  const [seeded, setSeeded] = useState(false)
  if (data?.url && !seeded) {
    setSeeded(true)
    setUrl(data.url)
  }

  const done = (text: string) => {
    setError('')
    setMessage(text)
    queryClient.invalidateQueries({ queryKey: ['admin', 'demo-video'] })
  }
  const failed = (e: Error) => { setMessage(''); setError(e.message) }

  const save = useMutation({
    mutationFn: (value: string) => adminApi.setDemoVideo(value),
    onSuccess: () => done('Demo reel updated. Pending tenants’ TVs pick it up on their next sync.'),
    onError: failed,
  })

  const upload = useMutation({
    mutationFn: (file: File) => adminApi.uploadDemoVideo(file),
    onSuccess: (res) => { setUrl(res.url); done(res.message) },
    onError: failed,
  })

  return (
    <div className="space-y-6 p-6 text-foreground lg:p-8">
      <PageHeader
        title="Universal Demo Reel"
        description="What every TV plays while its company is still awaiting approval"
      />

      <Feedback ok={message} error={error} />

      <section className="space-y-4 rounded-2xl border border-border bg-card p-5">
        <label className="block">
          <span className="mb-1.5 block text-xs font-semibold text-muted-foreground">Video URL</span>
          <div className="flex flex-wrap gap-2">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://… or /uploads/demo/…"
              className="min-w-0 flex-1 rounded-xl border border-border bg-muted px-4 py-2.5 text-sm text-foreground placeholder:text-muted-foreground outline-none focus:border-ring"
            />
            <button
              onClick={() => save.mutate(url.trim())}
              disabled={save.isPending || !url.trim()}
              className="flex items-center gap-2 rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-50"
            >
              <Save className="size-4" />
              {save.isPending ? 'Saving…' : 'Save'}
            </button>
          </div>
        </label>

        <div className="flex flex-wrap items-center gap-3">
          <input
            ref={fileInput}
            type="file"
            accept="video/mp4,video/webm,video/quicktime"
            className="hidden"
            onChange={(e) => {
              const file = e.target.files?.[0]
              if (file) upload.mutate(file)
              e.target.value = ''
            }}
          />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={upload.isPending}
            className="flex items-center gap-2 rounded-xl border border-border bg-muted px-4 py-2 text-sm text-muted-foreground transition-all hover:bg-muted disabled:opacity-50"
          >
            <UploadCloud className="size-4" />
            {upload.isPending ? 'Uploading…' : 'Upload a file'}
          </button>
          {data?.url && (
            <a
              href={data.url}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 text-xs text-foreground hover:text-foreground"
            >
              <ExternalLink className="size-3.5" />
              Open current reel
            </a>
          )}
        </div>

        <p className="text-xs text-muted-foreground">Accepted: .mp4, .webm, .mov, .m4v</p>
      </section>

      {!isLoading && data?.url && (
        <section className="overflow-hidden rounded-2xl border border-border bg-black">
          <video key={data.url} src={data.url} controls className="max-h-[420px] w-full" />
        </section>
      )}
    </div>
  )
}
