'use client'

import * as React from 'react'
import Link from 'next/link'
import { MoreVertical } from 'lucide-react'
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'

/**
 * The one card used by Content, Screens, Groups and Websites.
 *
 * Every grid on the console shows the same thing: a 16:9 preview with status badges over
 * it, a name, a metadata line, and an overflow menu. Keeping it in one place is what stops
 * the four pages drifting apart.
 */
export function AssetCard({
  href,
  preview,
  badges,
  cornerBadge,
  title,
  subtitle,
  menu,
  onClick,
}: {
  href?: string
  preview: React.ReactNode
  /** Top-left status pills, e.g. Online / Expired. */
  badges?: React.ReactNode
  /** Bottom-right marker, e.g. duration or content type. */
  cornerBadge?: React.ReactNode
  title: string
  subtitle?: React.ReactNode
  menu?: React.ReactNode
  onClick?: () => void
}) {
  const body = (
    <>
      <div className="bg-muted relative aspect-video overflow-hidden rounded-t-xl">
        {preview}
        {badges && <div className="absolute top-2.5 left-2.5 flex flex-wrap gap-1.5">{badges}</div>}
        {cornerBadge && <div className="absolute right-2.5 bottom-2.5">{cornerBadge}</div>}
      </div>
      <div className="flex items-start gap-2 p-4">
        <div className={cn('min-w-0 flex-1', menu && 'pr-9')}>
          <h2 className="text-foreground text-[15px] leading-snug font-medium break-words">{title}</h2>
          {subtitle && <div className="text-muted-foreground mt-1 text-sm">{subtitle}</div>}
        </div>
      </div>
    </>
  )

  return (
    <div className="ring-hairline bg-card group relative rounded-xl ring-1 transition-shadow hover:shadow-md">
      {href ? (
        <Link href={href} className="focus-visible:ring-ring block rounded-xl focus-visible:ring-2 focus-visible:outline-none">
          {body}
        </Link>
      ) : onClick ? (
        <button onClick={onClick} className="focus-visible:ring-ring block w-full cursor-pointer rounded-xl text-left focus-visible:ring-2 focus-visible:outline-none">
          {body}
        </button>
      ) : (
        body
      )}

      {menu && (
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={`Actions for ${title}`}
            className="text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-ring absolute right-2 bottom-3.5 grid size-8 cursor-pointer place-items-center rounded-lg transition-colors focus-visible:ring-2 focus-visible:outline-none"
          >
            <MoreVertical className="size-4" aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent>{menu}</DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

/** Small pill drawn over a preview image; needs its own contrast, not the page's. */
export function OverlayBadge({
  tone = 'neutral',
  className,
  ...props
}: React.ComponentProps<'span'> & { tone?: 'online' | 'offline' | 'danger' | 'neutral' }) {
  return (
    <span
      className={cn(
        'inline-flex h-6 items-center rounded-md px-2 text-[11px] font-semibold text-white shadow-sm',
        tone === 'online' && 'bg-emerald-600',
        tone === 'offline' && 'bg-slate-600',
        tone === 'danger' && 'bg-rose-600',
        tone === 'neutral' && 'bg-black/70',
        className,
      )}
      {...props}
    />
  )
}

/** Standard grid the four list pages share. */
export function AssetGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">{children}</div>
}
