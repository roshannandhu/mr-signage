'use client'

import type { ReactNode } from 'react'
import { AlertTriangle, CheckCircle, Clock, X, XCircle } from 'lucide-react'

/**
 * Shared pieces for the platform-admin pages.
 *
 * All five of them draw the same status pills, stat cards and feedback banners; without a
 * shared home they get copy-pasted and drift, which is how the previous two admin screens
 * ended up disagreeing about how to call the API.
 */

export const statusStyles: Record<string, { label: string; className: string; Icon: typeof CheckCircle }> = {
  active: { label: 'Active', className: 'text-emerald-700 dark:text-emerald-400 bg-emerald-500/15 border-emerald-500/30', Icon: CheckCircle },
  pending_approval: { label: 'Pending', className: 'text-amber-700 dark:text-amber-400 bg-amber-500/15 border-amber-500/30', Icon: Clock },
  suspended: { label: 'Suspended', className: 'text-rose-700 dark:text-rose-400 bg-rose-500/15 border-rose-500/30', Icon: XCircle },
  rejected: { label: 'Rejected', className: 'text-red-700 dark:text-red-400 bg-red-500/15 border-red-500/30', Icon: XCircle },
  // Screens. They share this pill with tenants, and drawing every screen that was not online as
  // "Suspended" told an operator a TV had been cut off when it was merely unplugged or unpaired.
  online: { label: 'Online', className: 'text-emerald-700 dark:text-emerald-400 bg-emerald-500/15 border-emerald-500/30', Icon: CheckCircle },
  offline: { label: 'Offline', className: 'text-muted-foreground bg-muted border-border', Icon: XCircle },
  waiting_pairing: { label: 'Waiting to pair', className: 'text-amber-700 dark:text-amber-400 bg-amber-500/15 border-amber-500/30', Icon: Clock },
}

export function StatusPill({ status }: { status: string }) {
  // An unrecognised status says what it is. The fallback used to be the Active style, so a status
  // this table had never heard of -- a read-only or cancelled workspace -- looked healthy.
  const style = statusStyles[status] ?? {
    label: status.replaceAll('_', ' '),
    className: 'text-muted-foreground bg-muted border-border',
    Icon: Clock,
  }
  const { Icon } = style
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-xs font-medium ${style.className}`}>
      <Icon className="size-3" />
      {style.label}
    </span>
  )
}

// Complete class strings, never interpolated.
//
// These used to be built as `bg-${color}-500/10`. Tailwind v4 discovers classes by scanning
// source text, and this project has no config file and no safelist -- so not one of those
// generated classes existed in the stylesheet and every stat card rendered unstyled.
const accents = {
  violet: { tile: 'bg-accent border-border', icon: 'text-foreground' },
  emerald: { tile: 'bg-emerald-500/10 border-emerald-500/20', icon: 'text-emerald-700 dark:text-emerald-400' },
  amber: { tile: 'bg-amber-500/10 border-amber-500/20', icon: 'text-amber-700 dark:text-amber-400' },
  cyan: { tile: 'bg-cyan-500/10 border-cyan-500/20', icon: 'text-cyan-700 dark:text-cyan-400' },
  rose: { tile: 'bg-rose-500/10 border-rose-500/20', icon: 'text-rose-700 dark:text-rose-400' },
} as const

export type Accent = keyof typeof accents

export function StatCard({
  label, value, icon: Icon, accent = 'violet',
}: { label: string; value: ReactNode; icon: React.ElementType; accent?: Accent }) {
  const style = accents[accent]
  return (
    <div className="flex items-center gap-2.5 sm:gap-3 rounded-2xl border border-border bg-card p-3 sm:p-4">
      <div className={`grid size-8 sm:size-9 shrink-0 place-items-center rounded-xl border ${style.tile}`}>
        <Icon className={`size-3.5 sm:size-4 ${style.icon}`} />
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-lg sm:text-xl font-bold tracking-tight text-foreground">{value}</p>
        <p className="text-[11px] sm:text-xs text-muted-foreground line-clamp-2 leading-tight break-words">{label}</p>
      </div>
    </div>
  )
}

export function QuotaBar({ used, max }: { used: number; max: number | null }) {
  // null is "no limit configured", 0 is "a package that grants none". This used to treat
  // 0 as unlimited, which was right for the raw override column and wrong for the limit
  // now reported: every tenant's override is 0, so the console drew an infinity sign for
  // the whole estate while the packages behind them were capped.
  const unlimited = max === null || max === undefined
  const pct = unlimited || max === 0 ? 100 : Math.min(100, Math.round((used / max) * 100))
  const barColor = pct >= 100 ? 'bg-rose-500' : pct >= 80 ? 'bg-amber-500' : 'bg-primary'
  // Unlimited is never "at the limit": pct is 100 there only so no bar is drawn.
  const textColor = unlimited ? 'text-muted-foreground'
    : pct >= 100 ? 'text-rose-700 dark:text-rose-400 font-semibold'
      : pct >= 80 ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className={textColor}>{used} / {unlimited ? '∞' : max}</span>
        {!unlimited && <span className="text-muted-foreground">{pct}%</span>}
      </div>
      {!unlimited && (
        <div className="h-1 overflow-hidden rounded-full bg-muted">
          <div className={`h-full rounded-full transition-all ${barColor}`} style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  )
}

export function Feedback({ ok, error, onDismiss }: { ok?: string; error?: string; onDismiss?: () => void }) {
  if (!ok && !error) return null
  return (
    <div
      role="status"
      className={`flex items-start justify-between gap-2.5 rounded-xl border p-3 text-xs sm:text-sm leading-relaxed ${
        error
          ? 'border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-400'
          : 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
      }`}
    >
      <div className="flex items-start gap-2 min-w-0 flex-1">
        {error ? (
          <AlertTriangle className="size-4 shrink-0 mt-0.5 text-rose-600 dark:text-rose-400" />
        ) : (
          <CheckCircle className="size-4 shrink-0 mt-0.5 text-emerald-600 dark:text-emerald-400" />
        )}
        <span className="break-words">{error || ok}</span>
      </div>
      {onDismiss && (
        <button
          type="button"
          onClick={onDismiss}
          className="shrink-0 rounded p-0.5 opacity-70 hover:opacity-100 transition-opacity"
          aria-label="Dismiss feedback"
        >
          <X className="size-3.5" />
        </button>
      )}
    </div>
  )
}

export function PageHeader({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div className="min-w-0">
        <h1 className="text-xl font-bold tracking-tight text-foreground sm:text-2xl">{title}</h1>
        {description && <p className="mt-0.5 text-xs text-muted-foreground sm:text-sm leading-relaxed">{description}</p>}
      </div>
      {children && <div className="flex flex-wrap items-center gap-2 shrink-0">{children}</div>}
    </div>
  )
}

/** What the API takes for "no limit" on every plan limit; the server stores it where each column reads it. */
export const UNLIMITED = -1

/**
 * The feature toggles the app actually understands, shared by every place that edits a
 * plan's or a custom request's flags (packages, the grant-limits dialog, custom requests):
 * one fixed list so an operator sets flags the code reads (billing.plan_features /
 * require_feature), not typos that silently gate nothing.
 *
 * `enforced: false` means the flag changes nothing in the product -- it is a commitment
 * made outside the software. Labelled rather than removed so nobody has to guess which
 * toggles bite.
 */
export const FEATURES: { key: string; label: string; enforced: boolean }[] = [
  { key: 'scheduling', label: 'Ad Booking & Scheduling', enforced: true },
  { key: 'transitions', label: 'Transitions', enforced: true },
  { key: 'payslips', label: 'Payslips & Invoices', enforced: true },
  { key: 'ads_counting', label: 'Ads counting reports', enforced: true },
  { key: 'pdf_reports', label: 'PDF playback reports', enforced: true },
  { key: 'remote_control', label: 'Remote control & alerts', enforced: true },
  { key: 'kiosk_lock', label: 'TV lock', enforced: true },
  { key: 'emergency_alert', label: 'Emergency alert', enforced: true },
  { key: 'priority_support', label: 'Priority support', enforced: false },
]

/**
 * A limit with an "Unlimited" box.
 *
 * `value` is in the stored unit (bytes for storage) or UNLIMITED; `scale` is how many stored
 * units make one displayed unit (GiB). Before this the form said "0 = unlimited" on all four,
 * which is true for ad slots, clients and storage but means ZERO screens -- so leaving the
 * screens box blank gave every tenant on the package "Screen limit reached (0/0)".
 */
export function LimitInput({
  value,
  onChange,
  scale = 1,
  width = 'w-full',
  label,
}: {
  value: number
  onChange: (next: number) => void
  scale?: number
  width?: string
  label: string
}) {
  const unlimited = value === UNLIMITED
  return (
    <div className="flex flex-col gap-1">
      <input
        type="number"
        min={0}
        aria-label={label}
        value={unlimited ? '' : Math.round(value / scale)}
        disabled={unlimited}
        placeholder={unlimited ? 'No limit' : undefined}
        onChange={(e) => onChange(Math.max(0, Number(e.target.value)) * scale)}
        className={`rounded-xl border border-border bg-muted px-3 py-2 text-sm text-foreground disabled:opacity-40 ${width}`}
      />
      <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <input
          type="checkbox"
          checked={unlimited}
          onChange={(e) => onChange(e.target.checked ? UNLIMITED : scale)}
          className="size-3.5 accent-foreground"
        />
        Unlimited
      </label>
    </div>
  )
}

export function formatBytes(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** i).toFixed(i === 0 ? 0 : 1)} ${units[i]}`
}

export function formatPaise(paise: number): string {
  return paise === 0 ? 'Free' : `₹${(paise / 100).toLocaleString('en-IN')}`
}
