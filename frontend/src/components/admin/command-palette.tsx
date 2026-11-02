'use client'

import React, { useEffect, useId, useMemo, useRef, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  Activity,
  AlertTriangle,
  CreditCard,
  ExternalLink,
  Loader2,
  Rocket,
  Search,
  SearchX,
  Users,
  X,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import type { SearchHit } from '@/lib/types'
import { cn } from '@/lib/utils'
import { Dialog, DialogOverlay, DialogPortal } from '@/components/ui/dialog'

// -----------------------------------------------------------------------------
// Category Metadata & Tokens
// -----------------------------------------------------------------------------

const TYPE_CONFIG: Record<
  string,
  { label: string; groupLabel: string; icon: React.ElementType; color: string }
> = {
  tenant: {
    label: 'Tenant',
    groupLabel: 'Tenants & Workspaces',
    icon: Users,
    color: 'text-indigo-600 dark:text-indigo-400 bg-indigo-500/10 border-indigo-500/20',
  },
  screen: {
    label: 'Screen',
    groupLabel: 'TV Screens & Fleet',
    icon: Activity,
    color: 'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border-emerald-500/20',
  },
  user: {
    label: 'User',
    groupLabel: 'Users & Operators',
    icon: Users,
    color: 'text-sky-600 dark:text-sky-400 bg-sky-500/10 border-sky-500/20',
  },
  payment: {
    label: 'Payment',
    groupLabel: 'Billing & Transactions',
    icon: CreditCard,
    color: 'text-amber-600 dark:text-amber-400 bg-amber-500/10 border-amber-500/20',
  },
  release: {
    label: 'Release',
    groupLabel: 'App Releases',
    icon: Rocket,
    color: 'text-fuchsia-600 dark:text-fuchsia-400 bg-fuchsia-500/10 border-fuchsia-500/20',
  },
}

// -----------------------------------------------------------------------------
// Inner Content (Mounted cleanly when open)
// -----------------------------------------------------------------------------

function CommandPaletteContent({ onOpenChange }: { onOpenChange: (open: boolean) => void }) {
  const router = useRouter()
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const inputId = useId()

  const [query, setQuery] = useState('')
  const [debouncedHits, setDebouncedHits] = useState<SearchHit[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [selectedIndex, setSelectedIndex] = useState(0)

  // Derived results: empty when query is under 2 chars
  const trimmed = query.trim()
  const results = useMemo(() => (trimmed.length < 2 ? [] : debouncedHits), [trimmed, debouncedHits])
  const isTooShort = trimmed.length < 2

  // Debounced search effect
  useEffect(() => {
    if (trimmed.length < 2) return

    let cancelled = false
    const timer = setTimeout(async () => {
      setLoading(true)
      setError(null)
      try {
        const response = await adminApi.search(trimmed)
        if (!cancelled) {
          setDebouncedHits(response.results ?? [])
          setSelectedIndex(0)
          setLoading(false)
        }
      } catch (err: unknown) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Search failed. Please try again.')
          setDebouncedHits([])
          setLoading(false)
        }
      }
    }, 150)

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [trimmed])

  const totalItems = results.length

  const handleSelect = (hit: SearchHit) => {
    onOpenChange(false)
    if (hit.href) {
      router.push(hit.href)
    }
  }

  // Keyboard navigation
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (totalItems > 0) {
        setSelectedIndex((prev) => (prev + 1) % totalItems)
      }
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      if (totalItems > 0) {
        setSelectedIndex((prev) => (prev - 1 + totalItems) % totalItems)
      }
    } else if (e.key === 'Enter') {
      e.preventDefault()
      if (totalItems > 0 && results[selectedIndex]) {
        handleSelect(results[selectedIndex])
      }
    } else if (e.key === 'Escape') {
      e.preventDefault()
      onOpenChange(false)
    }
  }

  // Auto-scroll highlighted option into view
  useEffect(() => {
    if (listRef.current) {
      const activeElement = listRef.current.querySelector<HTMLElement>(`[data-index="${selectedIndex}"]`)
      if (activeElement) {
        activeElement.scrollIntoView({ block: 'nearest' })
      }
    }
  }, [selectedIndex])

  // Group results by category
  const groupedResults = useMemo(() => {
    const map = new Map<string, { config: (typeof TYPE_CONFIG)[string]; items: { hit: SearchHit; globalIndex: number }[] }>()

    results.forEach((hit, index) => {
      const typeKey = hit.type in TYPE_CONFIG ? hit.type : 'tenant'
      const cfg = TYPE_CONFIG[typeKey] ?? {
        label: hit.type,
        groupLabel: hit.type.toUpperCase(),
        icon: Users,
        color: 'text-muted-foreground bg-muted border-border',
      }

      if (!map.has(typeKey)) {
        map.set(typeKey, { config: cfg, items: [] })
      }
      map.get(typeKey)!.items.push({ hit, globalIndex: index })
    })

    return Array.from(map.values())
  }, [results])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Global Admin Search"
      className={cn(
        'fixed left-1/2 top-[12%] z-50 w-full max-w-xl -translate-x-1/2 overflow-hidden',
        'rounded-2xl border border-border bg-card shadow-2xl text-foreground outline-none',
        'animate-in fade-in-0 zoom-in-95 duration-150',
      )}
    >
      {/* Search Header Bar */}
      <div className="relative flex items-center border-b border-border px-4 py-3">
        <Search className="size-4 shrink-0 text-muted-foreground mr-3" aria-hidden="true" />
        <label htmlFor={inputId} className="sr-only">
          Search admin platform
        </label>
        <input
          id={inputId}
          ref={inputRef}
          type="text"
          autoFocus
          role="combobox"
          aria-expanded={results.length > 0}
          aria-controls="admin-search-results"
          aria-activedescendant={results[selectedIndex] ? `search-item-${selectedIndex}` : undefined}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Search workspaces, screens, users, payments, releases…"
          className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground outline-none"
        />
        {loading && <Loader2 className="size-4 animate-spin text-muted-foreground ml-2 shrink-0" />}
        {query && !loading && (
          <button
            type="button"
            onClick={() => setQuery('')}
            aria-label="Clear query"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            <X className="size-3.5" />
          </button>
        )}
      </div>

      {/* Results Listbox */}
      <div
        id="admin-search-results"
        ref={listRef}
        role="listbox"
        className="max-h-96 overflow-y-auto p-2"
      >
        {/* Guide Hint when query is under 2 chars */}
        {isTooShort && !error && (
          <div className="py-8 text-center text-xs text-muted-foreground">
            <p className="font-medium text-foreground">Quick Search</p>
            <p className="mt-1">Type 2 or more characters to search across tenants, devices, users, payments, and releases.</p>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="flex items-center gap-2 rounded-xl border border-rose-500/20 bg-rose-500/10 p-4 text-xs text-rose-700 dark:text-rose-400">
            <AlertTriangle className="size-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Empty State */}
        {!isTooShort && !loading && results.length === 0 && !error && (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <SearchX className="size-8 text-muted-foreground/60 mb-2" />
            <p className="text-sm font-semibold text-foreground">No results found</p>
            <p className="text-xs text-muted-foreground mt-0.5 max-w-xs">
              No matching records found for &ldquo;{query}&rdquo;. Check spelling or try a slug, device ID, or email.
            </p>
          </div>
        )}

        {/* Grouped Search Results */}
        {groupedResults.map((group) => {
          const Icon = group.config.icon
          return (
            <div key={group.config.groupLabel} className="mb-2">
              <div className="flex items-center gap-1.5 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                <Icon className="size-3" />
                <span>{group.config.groupLabel}</span>
              </div>
              <div className="space-y-0.5">
                {group.items.map(({ hit, globalIndex }) => {
                  const isHighlighted = globalIndex === selectedIndex
                  const HitIcon = group.config.icon
                  return (
                    <div
                      key={`${hit.type}-${hit.id}-${globalIndex}`}
                      id={`search-item-${globalIndex}`}
                      role="option"
                      aria-selected={isHighlighted}
                      data-index={globalIndex}
                      onClick={() => handleSelect(hit)}
                      onMouseEnter={() => setSelectedIndex(globalIndex)}
                      className={cn(
                        'flex cursor-pointer items-center justify-between gap-3 rounded-xl px-3 py-2 text-sm transition-colors',
                        isHighlighted
                          ? 'bg-accent text-foreground'
                          : 'text-muted-foreground hover:bg-muted hover:text-foreground',
                      )}
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <div className={cn('grid size-7 shrink-0 place-items-center rounded-lg border', group.config.color)}>
                          <HitIcon className="size-3.5" />
                        </div>
                        <div className="min-w-0">
                          <p className="truncate text-xs font-semibold text-foreground leading-tight">
                            {hit.label}
                          </p>
                          {hit.sublabel && (
                            <p className="truncate font-mono text-[11px] text-muted-foreground leading-tight">
                              {hit.sublabel}
                            </p>
                          )}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <span className="rounded border border-border bg-muted/60 px-1.5 py-0.5 text-[10px] font-medium text-muted-foreground">
                          {group.config.label}
                        </span>
                        {isHighlighted && (
                          <ExternalLink className="size-3 text-muted-foreground" aria-hidden="true" />
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer Bar with Keyboard Hints */}
      <div className="flex items-center justify-between border-t border-border bg-muted/40 px-4 py-2 text-[11px] text-muted-foreground">
        <div className="flex items-center gap-3">
          <span>
            <kbd className="rounded border border-border bg-card px-1.5 py-0.5 text-[10px] font-mono">↑↓</kbd> to navigate
          </span>
          <span>
            <kbd className="rounded border border-border bg-card px-1.5 py-0.5 text-[10px] font-mono">↵</kbd> to select
          </span>
          <span>
            <kbd className="rounded border border-border bg-card px-1.5 py-0.5 text-[10px] font-mono">esc</kbd> to close
          </span>
        </div>
        {totalItems > 0 && (
          <span>
            {totalItems} hit{totalItems === 1 ? '' : 's'}
          </span>
        )}
      </div>
    </div>
  )
}

// -----------------------------------------------------------------------------
// Command Palette Export
// -----------------------------------------------------------------------------

export interface CommandPaletteProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CommandPalette({ open, onOpenChange }: CommandPaletteProps) {
  if (!open) return null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogPortal>
        <DialogOverlay className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm transition-opacity" />
        <CommandPaletteContent onOpenChange={onOpenChange} />
      </DialogPortal>
    </Dialog>
  )
}

// -----------------------------------------------------------------------------
// Header Search Trigger Button
// -----------------------------------------------------------------------------

export function CommandPaletteTrigger({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Search admin platform (Ctrl+K / Cmd+K)"
      className={cn(
        'group flex items-center gap-2 rounded-xl border border-border bg-muted/60 px-2.5 py-1.5 text-xs text-muted-foreground',
        'transition-colors hover:border-ring hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring cursor-pointer',
      )}
    >
      <Search className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="hidden sm:inline">Search platform…</span>
      <kbd className="hidden sm:inline-flex items-center rounded border border-border bg-background px-1.5 py-0.5 text-[10px] font-medium tracking-tight text-muted-foreground group-hover:text-foreground">
        ⌘K
      </kbd>
    </button>
  )
}
