'use client'

import { useEffect, useMemo, useRef, useState, memo, type CSSProperties, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { closestCenter, type CollisionDetection, DndContext, DragOverlay, type DragEndEvent, type DragStartEvent, KeyboardSensor, MouseSensor, TouchSensor, pointerWithin, useDraggable, useDroppable, useSensor, useSensors } from '@dnd-kit/core'
import { arrayMove, SortableContext, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { CalendarClock, Check, ChevronDown, ChevronUp, Clock3, GripVertical, ListVideo, Plus, Search, Sparkles, Trash2, X } from 'lucide-react'
import { Dialog as BaseDialog } from '@base-ui/react/dialog'
import { toast } from 'sonner'
import { EmptyState } from '@/components/dashboard/empty-state'
import { ErrorState } from '@/components/dashboard/error-state'
import { BookOrExtendDialog } from '@/components/dashboard/book-or-extend-dialog'
import { MediaThumbnail } from '@/components/dashboard/media-thumbnail'
import { PageHeader } from '@/components/dashboard/page-header'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { api } from '@/lib/api'
import { assetOrientation, clipDuration, dateTimeLocal, loopDuration } from '@/lib/format'
import { useAuthStore } from '@/lib/store'
import { canEditTenantContent } from '@/lib/roles'
import { usePlanFeatures } from '@/lib/use-plan-features'
import { cn } from '@/lib/utils'
import type { ContentItem, Playlist, PlaylistItem, TransitionName } from '@/lib/types'

const transitionOptions: { value: TransitionName; label: string; description: string }[] = [
  { value: 'none', label: 'Cut', description: 'Immediate handoff' },
  { value: 'fade', label: 'Fade', description: 'Soft crossfade' },
  { value: 'slide_left', label: 'Slide left', description: 'Next ad enters from right' },
  { value: 'slide_right', label: 'Slide right', description: 'Next ad enters from left' },
  { value: 'slide_up', label: 'Slide up', description: 'Next ad enters from below' },
  { value: 'slide_down', label: 'Slide down', description: 'Next ad enters from above' },
  { value: 'zoom', label: 'Zoom', description: 'Subtle depth change' },
]

// '' means "follow whatever the screen is set to"; a number overrides it for this item
// only, which is how one portrait advert plays correctly on a landscape wall.
const rotationOptions = [
  { value: '', label: 'Follow screen' },
  { value: '0', label: 'Landscape (0°)' },
  { value: '90', label: 'Portrait (90°)' },
  { value: '180', label: 'Landscape flipped (180°)' },
  { value: '270', label: 'Portrait flipped (270°)' },
]
const rotationLabel = (value: string | null) =>
  rotationOptions.find((option) => option.value === (value ?? ''))?.label || 'Follow screen'

const transitionLabel = (value: TransitionName) => transitionOptions.find((option) => option.value === value)?.label || value

type Selection = TransitionName | 'inherit'

const shortDate = (value?: string | null) => (value ? new Date(value).toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '')

function previewStyle(type: TransitionName, incoming: boolean, phase: boolean, durationMs: number): CSSProperties {
  const style: CSSProperties = {
    opacity: 1,
    transform: 'translate3d(0,0,0) scale(1)',
    transitionProperty: 'transform, opacity',
    transitionDuration: `${durationMs}ms`,
    transitionTimingFunction: 'cubic-bezier(0.2, 0.8, 0.2, 1)',
  }
  if (!phase) {
    if (type === 'fade') style.opacity = incoming ? 0 : 1
    if (type === 'slide_left') style.transform = incoming ? 'translate3d(100%,0,0)' : 'translate3d(0,0,0)'
    if (type === 'slide_right') style.transform = incoming ? 'translate3d(-100%,0,0)' : 'translate3d(0,0,0)'
    if (type === 'slide_up') style.transform = incoming ? 'translate3d(0,100%,0)' : 'translate3d(0,0,0)'
    if (type === 'slide_down') style.transform = incoming ? 'translate3d(0,-100%,0)' : 'translate3d(0,0,0)'
    if (type === 'zoom') {
      style.opacity = incoming ? 0 : 1
      style.transform = incoming ? 'translate3d(0,0,0) scale(0.7)' : 'translate3d(0,0,0) scale(1)'
    }
  } else {
    if (type === 'fade') style.opacity = incoming ? 1 : 0
    if (type === 'slide_left') style.transform = incoming ? 'translate3d(0,0,0)' : 'translate3d(-100%,0,0)'
    if (type === 'slide_right') style.transform = incoming ? 'translate3d(0,0,0)' : 'translate3d(100%,0,0)'
    if (type === 'slide_up') style.transform = incoming ? 'translate3d(0,0,0)' : 'translate3d(0,-100%,0)'
    if (type === 'slide_down') style.transform = incoming ? 'translate3d(0,0,0)' : 'translate3d(0,100%,0)'
    if (type === 'zoom') {
      style.opacity = incoming ? 1 : 0
      style.transform = incoming ? 'translate3d(0,0,0) scale(1)' : 'translate3d(0,0,0) scale(1.3)'
    }
  }
  return style
}

function TransitionPreview({ transition, durationMs, compact = false }: { transition: TransitionName; durationMs: number; compact?: boolean }) {
  const [phase, setPhase] = useState(false)
  useEffect(() => {
    if (transition === 'none') return
    const interval = setInterval(() => setPhase((current) => !current), Math.max(durationMs + 700, 1500))
    return () => clearInterval(interval)
  }, [transition, durationMs])
  return (
    <div className={cn('relative overflow-hidden rounded-lg border border-hairline bg-slate-950 font-mono text-white shadow-xs', compact ? 'h-9 w-20' : 'h-12 w-24')}>
      <div style={previewStyle(transition, false, phase, durationMs)} className="absolute inset-0 flex items-center justify-center bg-slate-800 text-[10px] font-semibold tracking-wider text-slate-200">AD 1</div>
      <div style={previewStyle(transition, true, phase, durationMs)} className="absolute inset-0 flex items-center justify-center bg-primary text-[10px] font-semibold tracking-wider text-white">AD 2</div>
    </div>
  )
}

function DefaultTransitionPanel({ playlist, disabled, pending, onSave }: {
  playlist: Playlist
  disabled: boolean
  pending: boolean
  onSave: (transition: TransitionName, durationMs: number, applyToAll: boolean) => void
}) {
  const [transition, setTransition] = useState<TransitionName>(playlist.default_transition)
  const [durationMs, setDurationMs] = useState(playlist.default_transition_ms)
  const dirty = transition !== playlist.default_transition || durationMs !== playlist.default_transition_ms
  return (
    <Card className="mb-5 border-hairline bg-card/70 shadow-[0_1px_2px_rgba(15,23,42,.04)]">
      <CardContent className="p-4 sm:p-5 space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="grid size-8 place-items-center rounded-lg bg-primary/10 text-primary shrink-0">
              <Sparkles className="size-4" />
            </div>
            <div>
              <p className="text-sm font-semibold text-foreground">Default handoff transition</p>
              <p className="text-muted-foreground/80 text-xs">
                Applies to all ads without custom transitions.
                {playlist.items.length < 2 && (
                  <span className="ml-1 text-amber-600 dark:text-amber-400 font-medium">
                    (Active between 2+ ads)
                  </span>
                )}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 pt-1 sm:pt-0">
            <Button size="sm" variant="outline" disabled={disabled || pending || !dirty} onClick={() => onSave(transition, durationMs, false)}>
              Save default
            </Button>
            <Button size="sm" variant="default" disabled={disabled || pending} onClick={() => onSave(transition, durationMs, true)}>
              <Sparkles data-icon="inline-start" /> Apply to all items
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,180px)_minmax(0,1fr)_auto] sm:items-center pt-1 border-t border-hairline">
          <div>
            <Label className="text-muted-foreground mb-1.5 block text-xs font-medium">Transition</Label>
            <Select value={transition} onValueChange={(value: TransitionName | null) => value && setTransition(value)} disabled={disabled}>
              <SelectTrigger className="w-full">
                <SelectValue>{(value: TransitionName | null) => (value ? transitionLabel(value) : 'Choose transition')}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {transitionOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <Label htmlFor="default-transition-duration" className="text-muted-foreground text-xs font-medium">Handoff duration</Label>
              <span className="text-muted-foreground font-mono text-xs font-semibold">{durationMs} ms</span>
            </div>
            <input
              id="default-transition-duration"
              type="range"
              min={100}
              max={3000}
              step={50}
              value={durationMs}
              disabled={disabled}
              onChange={(event) => setDurationMs(Number(event.target.value))}
              className="accent-primary h-8 w-full cursor-pointer"
            />
          </div>

          <div className="flex flex-row sm:flex-col items-center sm:items-end justify-between sm:justify-center gap-2 pt-1 sm:pt-0">
            <span className="text-muted-foreground text-xs font-medium sm:hidden">Live preview:</span>
            <TransitionPreview transition={transition} durationMs={durationMs} compact />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

// What the editor shows for an item, in the shape its inputs edit.
function formFor(item: PlaylistItem, defaultDurationMs: number) {
  return {
    duration: item.duration,
    startAt: dateTimeLocal(item.start_at),
    endAt: dateTimeLocal(item.end_at),
    days: item.schedule?.days_of_week || [],
    windowStart: item.schedule?.start_time || '',
    windowEnd: item.schedule?.end_time || '',
    selection: (item.transition ?? 'inherit') as Selection,
    transitionMs: item.transition_ms ?? defaultDurationMs,
    rotation: item.rotation === null || item.rotation === undefined ? '' : String(item.rotation),
  }
}

// One row per item. Everything below the summary line is collapsed by default: with
// the transition and schedule editors both always open, a twenty-item loop rendered
// forty selects, forty sliders and a hundred and forty day toggles on one screen —
// and each item needed two separate saves to commit one change.
function ItemRow({
  item,
  index,
  totalItems,
  defaultTransition,
  defaultDurationMs,
  hasTransitions,
  hasCommercial,
  canEdit,
  saving,
  onMoveUp,
  onMoveDown,
  onRemove,
  onSave,
  highlighted = false,
}: {
  item: PlaylistItem
  index: number
  totalItems: number
  defaultTransition: TransitionName
  defaultDurationMs: number
  /** Whether the workspace's plan includes per-item transitions. False hides the whole
   * "Handoff to next ad" section rather than showing it locked -- a tenant should not learn
   * about a capability their admin has not turned on for them. */
  hasTransitions: boolean
  /** Whether the workspace's plan includes commercial advertising & date scheduling. */
  hasCommercial: boolean
  canEdit: boolean
  saving: boolean
  onMoveUp?: () => void
  onMoveDown?: () => void
  onRemove: () => void
  onSave: (data: {
    duration: number
    start_at: string | null
    end_at: string | null
    schedule: { days_of_week: number[]; start_time: string | null; end_time: string | null } | null
    transition: TransitionName | null
    transition_ms: number | null
    rotation: number | null
  }) => void
  highlighted?: boolean
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: item.id, disabled: !canEdit })
  const [open, setOpen] = useState(false)

  const [form, setForm] = useState(() => formFor(item, defaultDurationMs))

  // The form is a copy of the saved item, so it has to follow the item when something else
  // changes it. "Apply to all items" rewrites every transition, and a row that kept the one it
  // mounted with sent it straight back the next time any other field was saved. Re-seeded while
  // rendering rather than in an effect, so the row stays open and there is no stale frame.
  const savedForm = JSON.stringify(formFor(item, defaultDurationMs))
  const [seenForm, setSeenForm] = useState(savedForm)
  if (seenForm !== savedForm) {
    setSeenForm(savedForm)
    setForm(formFor(item, defaultDurationMs))
  }

  const dirty =
    form.duration !== item.duration ||
    form.startAt !== dateTimeLocal(item.start_at) ||
    form.endAt !== dateTimeLocal(item.end_at) ||
    form.selection !== (item.transition ?? 'inherit') ||
    (form.selection !== 'inherit' && form.transitionMs !== (item.transition_ms ?? defaultDurationMs)) ||
    form.rotation !== (item.rotation === null || item.rotation === undefined ? '' : String(item.rotation))

  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  const effectiveTransition = form.selection === 'inherit' ? defaultTransition : form.selection
  const effectiveMs = form.selection === 'inherit' ? defaultDurationMs : form.transitionMs
  const scheduled = Boolean(item.start_at || item.end_at || item.schedule)

  const save = () => {
    if (form.startAt && form.endAt && form.endAt <= form.startAt) {
      toast.error('The end date must be after the start date')
      return
    }
    const hasWindow = Boolean(form.days.length || form.windowStart || form.windowEnd)
    onSave({
      duration: form.duration,
      start_at: form.startAt || null,
      end_at: form.endAt || null,
      schedule: hasWindow ? { days_of_week: form.days, start_time: form.windowStart || null, end_time: form.windowEnd || null } : null,
      transition: form.selection === 'inherit' ? null : form.selection,
      transition_ms: form.selection === 'inherit' ? null : form.transitionMs,
      rotation: form.rotation === '' ? null : Number(form.rotation),
    })
  }

  return (
    <Card
      id={`timeline-item-${item.id}`}
      ref={setNodeRef}
      style={{ transform: CSS.Translate.toString(transform), transition }}
      className={cn(
        'border-0 bg-card py-0 ring-1 ring-hairline transition-all duration-300',
        isDragging ? 'z-30 opacity-40 shadow-2xl ring-2 ring-primary' : 'shadow-[0_1px_2px_rgba(15,23,42,.04)]',
        item.id < 0 && 'animate-in fade-in zoom-in-95 ring-2 ring-primary/40 bg-primary/5 duration-500',
        highlighted && 'ring-2 ring-primary bg-primary/5 shadow-lg shadow-primary/25 scale-[1.015]'
      )}
    >
      <CardContent className="p-3 sm:p-5">
        <div className="flex items-center gap-2.5 sm:gap-3">
          {/* Reordering Controls Pod: Clean Move Up, Drag Handle, Move Down */}
          <div className="flex flex-col items-center justify-center shrink-0 rounded-lg bg-muted/60 p-1 border border-hairline gap-0.5">
            <button
              type="button"
              disabled={!canEdit || index === 0}
              onClick={(e) => {
                e.stopPropagation()
                onMoveUp?.()
              }}
              className="flex size-7 items-center justify-center rounded text-muted-foreground/70 hover:bg-background hover:text-foreground active:scale-90 disabled:opacity-20 disabled:pointer-events-none transition-all touch-manipulation"
              aria-label={`Move ${item.content.name} up`}
              title="Move up"
            >
              <ChevronUp className="size-4" />
            </button>
            <button
              type="button"
              {...attributes}
              {...listeners}
              disabled={!canEdit}
              className="grid size-7 shrink-0 cursor-grab touch-none place-items-center rounded text-muted-foreground/80 hover:bg-background hover:text-foreground active:cursor-grabbing disabled:cursor-default focus-visible:ring-ring focus-visible:ring-1 focus-visible:outline-none select-none transition-all touch-manipulation"
              aria-label={`Drag to reorder ${item.content.name}`}
              title="Drag to reorder"
            >
              <GripVertical className="size-4" />
            </button>
            <button
              type="button"
              disabled={!canEdit || index >= totalItems - 1}
              onClick={(e) => {
                e.stopPropagation()
                onMoveDown?.()
              }}
              className="flex size-7 items-center justify-center rounded text-muted-foreground/70 hover:bg-background hover:text-foreground active:scale-90 disabled:opacity-20 disabled:pointer-events-none transition-all touch-manipulation"
              aria-label={`Move ${item.content.name} down`}
              title="Move down"
            >
              <ChevronDown className="size-4" />
            </button>
          </div>

          {/* Thumbnail with Sequence Badge */}
          <div className="relative size-14 sm:size-16 shrink-0">
            <MediaThumbnail item={item.content} className="size-14 sm:size-16 rounded-xl object-cover" />
            <span className="absolute left-1 top-1 flex h-4 min-w-4 items-center justify-center rounded bg-black/80 px-1 font-mono text-[9px] font-semibold text-white shadow-sm ring-1 ring-white/10 backdrop-blur-xs">
              #{index + 1}
            </span>
          </div>

          <div className="min-w-0 flex-1">
            <p className="text-foreground truncate font-semibold text-sm sm:text-base">{item.content.name}</p>
            <div className="text-muted-foreground/70 mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
              <span className="inline-flex items-center gap-1 font-medium text-foreground/80">
                <Clock3 className="size-3 text-muted-foreground" /> {loopDuration(item.duration)}
                {item.content.type === 'video' && clipDuration(item.content.duration_ms) && (
                  <span className="text-muted-foreground/60"> ({clipDuration(item.content.duration_ms)})</span>
                )}
              </span>
              <span aria-hidden="true" className="text-muted-foreground/40">·</span>
              <span className="inline-flex items-center gap-1 rounded bg-muted/60 px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground truncate max-w-[150px] sm:max-w-none">
                <Sparkles className="size-2.5 text-primary" /> {item.transition ? transitionLabel(item.transition) : `${transitionLabel(defaultTransition)} (default)`}
              </span>
              {hasCommercial && (
                <>
                  <span aria-hidden="true" className="text-muted-foreground/40">·</span>
                  {item.start_at || item.end_at || item.schedule ? (
                    <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/10 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:text-amber-300 ring-1 ring-amber-500/20">
                      <span className="size-1.5 rounded-full bg-amber-500" /> Scheduled
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 rounded-full bg-emerald-500/10 px-2 py-0.5 text-[11px] font-medium text-emerald-700 dark:text-emerald-300 ring-1 ring-emerald-500/20">
                      <span className="size-1.5 rounded-full bg-emerald-500" /> Always active
                    </span>
                  )}
                </>
              )}
            </div>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            <Button
              variant={open ? 'secondary' : 'outline'}
              size="sm"
              aria-expanded={open}
              aria-controls={`item-settings-${item.id}`}
              onClick={() => setOpen((current) => !current)}
              className="text-foreground shrink-0 h-8 px-2 sm:px-3 text-xs"
            >
              <ChevronDown className={cn('size-3.5 transition-transform mr-1', open && 'rotate-180')} aria-hidden="true" />
              <span>{open ? 'Close' : 'Edit'}</span>
            </Button>

            <div className="h-5 w-px bg-border/60 mx-0.5 hidden sm:block" />

            {canEdit && (
              <Button
                variant="ghost"
                size="icon"
                className="text-muted-foreground/40 hover:bg-destructive/10 hover:text-destructive shrink-0 size-8"
                onClick={onRemove}
                aria-label={`Remove ${item.content.name}`}
                title="Remove from loop"
              >
                <Trash2 className="size-4" />
              </Button>
            )}
          </div>
        </div>

        {open && (
          <div id={`item-settings-${item.id}`} className="border-hairline mt-5 border-t pt-5">
            <fieldset disabled={!canEdit || saving} className={cn('grid grid-cols-1 gap-5', hasTransitions && 'lg:grid-cols-2')}>
              {hasTransitions && (
                <div>
                  <p className="text-muted-foreground mb-3 flex items-center gap-2 text-xs font-semibold"><Sparkles className="text-primary size-3.5" /> Handoff to next ad</p>
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_96px] sm:items-end">
                    <div>
                      <Label className="text-muted-foreground mb-2 text-xs">Transition</Label>
                      <Select value={form.selection} onValueChange={(value: Selection | null) => value && set('selection', value)}>
                        <SelectTrigger className="w-full"><SelectValue>{(value: Selection | null) => value === 'inherit' ? `Use default (${transitionLabel(defaultTransition)})` : value ? transitionLabel(value) : 'Choose transition'}</SelectValue></SelectTrigger>
                        <SelectContent>
                          <SelectItem value="inherit">Use playlist default</SelectItem>
                          {transitionOptions.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
                        </SelectContent>
                      </Select>
                    </div>
                    <TransitionPreview transition={effectiveTransition} durationMs={effectiveMs} compact />
                  </div>
                  <div className={cn('mt-3', form.selection === 'inherit' && 'opacity-50')}>
                    <div className="mb-2 flex items-center justify-between">
                      <Label htmlFor={`transition-duration-${item.id}`} className="text-muted-foreground text-xs">Handoff duration</Label>
                      <span className="text-muted-foreground font-mono text-xs">{effectiveMs} ms</span>
                    </div>
                    <input id={`transition-duration-${item.id}`} type="range" min={100} max={3000} step={50} value={form.transitionMs} disabled={!canEdit || saving || form.selection === 'inherit'} onChange={(event) => set('transitionMs', Number(event.target.value))} className="accent-primary h-10 w-full" />
                  </div>
                </div>
              )}

              <div>
                <div className="mb-3 flex items-center justify-between">
                  <p className="text-muted-foreground flex items-center gap-2 text-xs font-semibold"><CalendarClock className="text-primary size-3.5" /> Playback rules</p>
                  {hasCommercial && <Badge variant={scheduled ? 'warning' : 'success'}>{scheduled ? 'Scheduled' : 'Always active'}</Badge>}
                </div>
                <div className={cn('grid grid-cols-1 gap-3', hasCommercial ? 'sm:grid-cols-3' : 'sm:grid-cols-2')}>
                  <div>
                    <Label htmlFor={`duration-${item.id}`} className="text-muted-foreground mb-2 text-xs">Seconds</Label>
                    {item.content.type === 'video' ? (
                      // A video's on-screen time is its own length — there is nothing to
                      // choose, so showing an editable box only invites a wrong answer.
                      <p className="border-input text-muted-foreground flex h-10 items-center rounded-lg border px-3 text-sm">
                        Plays the full clip{clipDuration(item.content.duration_ms) && ` (${clipDuration(item.content.duration_ms)})`}
                      </p>
                    ) : (
                      <Input id={`duration-${item.id}`} type="number" min={1} max={86400} value={form.duration} onChange={(event) => set('duration', Math.max(1, Number(event.target.value)))} />
                    )}
                  </div>
                  {hasCommercial ? (
                    <>
                      <div>
                        <Label htmlFor={`start-${item.id}`} className="text-muted-foreground mb-2 text-xs">Starts</Label>
                        <Input id={`start-${item.id}`} type="datetime-local" value={form.startAt} onChange={(event) => set('startAt', event.target.value)} />
                      </div>
                      <div>
                        <Label htmlFor={`end-${item.id}`} className="text-muted-foreground mb-2 text-xs">Expires</Label>
                        <Input id={`end-${item.id}`} type="datetime-local" value={form.endAt} onChange={(event) => set('endAt', event.target.value)} />
                      </div>
                    </>
                  ) : (
                    <div>
                      <Label className="text-muted-foreground mb-2 text-xs">Rotation</Label>
                      <Select value={form.rotation} onValueChange={(value: string | null) => set('rotation', value ?? '')}>
                        <SelectTrigger className="w-full">
                          <SelectValue>{(value: string | null) => rotationLabel(value)}</SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {rotationOptions.map((option) => (
                            <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  )}
                </div>
                {hasCommercial && (
                  <div className="mt-3">
                    <Label className="text-muted-foreground mb-2 text-xs">Rotation</Label>
                    <Select value={form.rotation} onValueChange={(value: string | null) => set('rotation', value ?? '')}>
                      <SelectTrigger className="w-full">
                        <SelectValue>{(value: string | null) => rotationLabel(value)}</SelectValue>
                      </SelectTrigger>
                      <SelectContent>
                        {rotationOptions.map((option) => (
                          <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                )}
              </div>
            </fieldset>

            {canEdit && (
              <div className="mt-5 flex items-center justify-end gap-3">
                {dirty && <span className="text-muted-foreground text-xs">Unsaved changes</span>}
                <Button size="sm" variant="outline" disabled={!dirty || saving} onClick={save}>
                  {saving ? 'Saving…' : <><Check data-icon="inline-start" /> Save item</>}
                </Button>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

const MemoizedItemRow = memo(ItemRow)

/**
 * The timeline's drop zone.
 *
 * A separate component on purpose: useDroppable must run inside the DndContext subtree, and
 * PlaylistBuilder renders that provider rather than living under it — a hook in its body
 * registers against the default context, so the drag activates but nothing is ever "over".
 */
function TimelineDropZone({ children }: { children: ReactNode }) {
  const { setNodeRef, isOver } = useDroppable({ id: 'timeline' })
  return (
    <div
      ref={setNodeRef}
      className={cn('rounded-2xl transition-colors', isOver && 'bg-primary/5 ring-2 ring-primary/40 ring-offset-4 ring-offset-background')}
    >
      {children}
    </div>
  )
}

/**
 * Droppable target wrapping the Content Library.
 * Dropping a timeline item here returns it to the library (removes it from playback loop).
 */
function LibraryDropZone({
  children,
  isDraggingTimelineItem,
}: {
  children: ReactNode
  isDraggingTimelineItem: boolean
}) {
  const { setNodeRef, isOver } = useDroppable({ id: 'content-library-drop' })
  return (
    <aside
      ref={setNodeRef}
      className={cn(
        'rounded-2xl bg-card p-4 shadow-[0_1px_2px_rgba(15,23,42,.04)] ring-1 ring-hairline lg:sticky lg:top-20 transition-all duration-200',
        isDraggingTimelineItem && 'ring-2 ring-dashed ring-destructive/50 bg-destructive/5',
        isOver && isDraggingTimelineItem && 'ring-2 ring-destructive bg-destructive/10 scale-[1.01]'
      )}
      aria-labelledby="library-title"
    >
      {isDraggingTimelineItem && (
        <div
          className={cn(
            'mb-3 rounded-xl border p-2.5 text-center text-xs font-semibold transition-all',
            isOver
              ? 'border-destructive bg-destructive text-white shadow-md'
              : 'border-destructive/30 bg-destructive/10 text-destructive'
          )}
        >
          {isOver ? 'Drop here to remove from playback loop' : 'Drag here to return ad to library'}
        </div>
      )}
      {children}
    </aside>
  )
}

/**
 * A row in the content library.
 *
 * Extracted from an inline `libraryRow()` helper because it now calls useDraggable, and a
 * hook cannot live in a function invoked from inside .map() — the hook order would change
 * the moment either list changes length.
 */
function LibraryRow({ content, booked, placed, instant, adding, canEdit, fetchedAt, onAdd }: {
  content: ContentItem
  booked: boolean
  placed: boolean
  instant: boolean
  adding: boolean
  canEdit: boolean
  fetchedAt: number
  onAdd: () => void
}) {
  // Only `listeners` and the ref — deliberately not `attributes`. Those would put
  // role="button" tabIndex={0} on a div that already contains a real button, adding a junk
  // tab stop. The + button stays the keyboard path.
  const { listeners, setNodeRef, isDragging } = useDraggable({
    id: `lib-${content.id}`,
    data: { content, instant },
    disabled: !canEdit,
  })
  const startsLater = booked && Boolean(content.placement_starts_at) && new Date(content.placement_starts_at as string).getTime() > fetchedAt

  return (
    <div
      ref={setNodeRef}
      {...listeners}
      className={cn(
        'flex items-center gap-3 rounded-xl border border-hairline p-2.5 transition-all duration-300 hover:bg-muted animate-in fade-in duration-300',
        canEdit && 'cursor-grab touch-none active:cursor-grabbing',
        isDragging && 'opacity-40',
        placed && 'bg-muted/40'
      )}
    >
      <div className="relative size-12 shrink-0">
        <MediaThumbnail item={content} className="size-12 rounded-lg" />
        {clipDuration(content.duration_ms) && (
          <span className="absolute right-0 bottom-0 rounded bg-black/75 px-1 text-[9px] font-semibold text-white">
            {clipDuration(content.duration_ms)}
          </span>
        )}
      </div>
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-foreground">{content.name}</p>
        <p className="mt-0.5 truncate text-xs text-muted-foreground/70">
          {booked
            ? <span className="text-emerald-700 dark:text-emerald-400">{startsLater ? `Starts ${shortDate(content.placement_starts_at)}` : `Booked until ${shortDate(content.placement_ends_at)}`}</span>
            : <span className="capitalize">{content.type}{assetOrientation(content.renditions) && ` • ${assetOrientation(content.renditions)}`}</span>}
          {content.client_name && ` • ${content.client_name}`}
        </p>
      </div>
      <Button
        size="icon-sm"
        variant={instant ? 'default' : 'outline'}
        disabled={!canEdit || adding}
        onClick={onAdd}
        aria-label={instant ? `Add ${content.name} to this screen` : `Book ${content.name}`}
        className={cn(
          'transition-transform active:scale-90',
          placed && 'border-primary/40 text-primary hover:bg-primary/10'
        )}
      >
        <Plus className="size-4" />
      </Button>
    </div>
  )
}

function MobileLibrarySheet({
  open,
  onOpenChange,
  children,
  subtitle,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  children: ReactNode
  subtitle?: string
}) {
  return (
    <BaseDialog.Root open={open} onOpenChange={onOpenChange}>
      <BaseDialog.Portal>
        <BaseDialog.Backdrop className="fixed inset-0 z-50 bg-black/40 backdrop-blur-xs transition-opacity animate-in fade-in" />
        <BaseDialog.Popup className="bg-card fixed inset-x-0 bottom-0 z-50 flex max-h-[85vh] flex-col rounded-t-2xl border-t border-hairline shadow-2xl animate-in slide-in-from-bottom duration-300">
          <div className="flex items-center justify-center pt-3 pb-1">
            <div className="h-1.5 w-12 rounded-full bg-muted-foreground/30" />
          </div>
          <div className="flex items-center justify-between px-5 py-2.5 border-b border-hairline">
            <div>
              <BaseDialog.Title className="font-semibold text-base text-foreground">Content library</BaseDialog.Title>
              {subtitle && <p className="text-xs text-muted-foreground/70">{subtitle}</p>}
            </div>
            <BaseDialog.Close
              aria-label="Close library"
              className="text-muted-foreground hover:bg-muted hover:text-foreground grid size-8 cursor-pointer place-items-center rounded-lg"
            >
              <X className="size-4" />
            </BaseDialog.Close>
          </div>
          <div className="flex-1 overflow-y-auto p-4 space-y-4">
            {children}
          </div>
        </BaseDialog.Popup>
      </BaseDialog.Portal>
    </BaseDialog.Root>
  )
}

export function PlaylistBuilder({ playlistId, showHeader = true, screenId }: {
  playlistId: number
  showHeader?: boolean
  /** Set on a screen's own page. Booked adverts then join or leave THIS screen instantly. */
  screenId?: number
}) {
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.user)
  const canEdit = canEditTenantContent(user)
  const planFeatures = usePlanFeatures()
  const hasTransitions = planFeatures.has('transitions')
  const hasCommercial = planFeatures.hasCommercial
  const playlistKey = useMemo(() => ['playlist', playlistId], [playlistId])
  const playlistQuery = useQuery({ queryKey: playlistKey, queryFn: () => api.getPlaylist(playlistId), enabled: Number.isFinite(playlistId) })
  const contentQuery = useQuery({ queryKey: ['content'], queryFn: api.getContent })
  const [localOrder, setLocalOrder] = useState<number[] | null>(null)
  const [isDraggingTimelineItem, setIsDraggingTimelineItem] = useState(false)
  const [search, setSearch] = useState('')
  const [savingItem, setSavingItem] = useState<number | null>(null)
  const [highlightedItemId, setHighlightedItemId] = useState<number | null>(null)
  const [mobileLibraryOpen, setMobileLibraryOpen] = useState(false)

  const serverItems = useMemo(() => [...(playlistQuery.data?.items || [])].sort((a, b) => a.order - b.order), [playlistQuery.data?.items])

  // Synchronize local order with server silently without render flashing
  useEffect(() => {
    if (localOrder && serverItems.length === localOrder.length) {
      const isSynced = serverItems.every((it, idx) => it.id === localOrder[idx])
      if (isSynced) {
        setLocalOrder(null)
      }
    }
  }, [serverItems, localOrder])

  const items = useMemo(() => {
    if (!localOrder) return serverItems
    const byId = new Map(serverItems.map((item) => [item.id, item]))
    const ordered = localOrder.map((itemId) => byId.get(itemId)).filter((item): item is PlaylistItem => Boolean(item))
    return ordered.length === serverItems.length ? ordered : serverItems
  }, [localOrder, serverItems])

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 100, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  )
  const [dragging, setDragging] = useState<ContentItem | null>(null)

  /**
   * Library card drags (string IDs, pointer only) get pointerWithin so dropping back
   * into the library does not trigger an accidental drop.
   *
   * Timeline reorders (numeric IDs) get closestCenter, but if the pointer is over the
   * content library drop target, that takes priority so dropping returns the ad to the library.
   */
  const collisionDetection: CollisionDetection = (args) => {
    // If dragging a timeline item, prioritize content-library-drop when pointer is over it
    if (typeof args.active.id === 'number' && args.pointerCoordinates) {
      const pointerCollisions = pointerWithin(args)
      const libraryHit = pointerCollisions.find((c) => c.id === 'content-library-drop')
      if (libraryHit) return [libraryHit]
    }
    if (typeof args.active.id === 'string') {
      return args.pointerCoordinates ? pointerWithin(args) : closestCenter(args)
    }
    const itemContainers = args.droppableContainers.filter(
      (c) => c.id !== 'timeline' && c.id !== 'content-library-drop'
    )
    return closestCenter({
      ...args,
      droppableContainers: itemContainers,
    })
  }

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: playlistKey })
    queryClient.invalidateQueries({ queryKey: ['playlists'] })
  }

  const reorderTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const debouncedReorder = (order: number[]) => {
    if (reorderTimeoutRef.current) clearTimeout(reorderTimeoutRef.current)
    reorderTimeoutRef.current = setTimeout(() => {
      reorderMutation.mutate(order)
    }, 200)
  }

  const reorderMutation = useMutation({
    mutationFn: (orders: number[]) => api.reorderPlaylistItems(playlistId, orders),
    onMutate: async (newOrderIds: number[]) => {
      await queryClient.cancelQueries({ queryKey: playlistKey })
      const previous = queryClient.getQueryData<Playlist>(playlistKey)
      if (previous) {
        const itemMap = new Map(previous.items.map((it) => [it.id, it]))
        const reordered: PlaylistItem[] = []
        newOrderIds.forEach((id, idx) => {
          const item = itemMap.get(id)
          if (item) {
            reordered.push({ ...item, order: idx })
          }
        })
        previous.items.forEach((item) => {
          if (!newOrderIds.includes(item.id)) reordered.push(item)
        })
        queryClient.setQueryData<Playlist>(playlistKey, {
          ...previous,
          items: reordered,
        })
      }
      return { previous }
    },
    onError: (error: Error, _vars, context) => {
      restore(context?.previous)
      setLocalOrder(null)
      toast.error(error.message)
      playlistQuery.refetch()
    },
    onSuccess: () => {
      toast.success('Order saved')
    },
    onSettled: () => {
      invalidate()
    },
  })
  // An advert with no running booking opens the booking dialog: it may not reach a screen
  // without a client, because the placement is what records the sale, the paid window and the
  // proof-of-play an invoice is built from.
  const [bookingFor, setBookingFor] = useState<ContentItem | null>(null)
  // Screens the operator would expect an advert booked from THIS loop to appear on.
  // effective_playlist_id, not playlist_id, so a screen that inherits this playlist from
  // its group counts -- that is still a screen showing this loop.
  const screensQuery = useQuery({ queryKey: ['screens'], queryFn: api.getScreens })
  const screensShowingThisPlaylist = useMemo(
    () => (screensQuery.data || []).filter((screen) => screen.effective_playlist_id === playlistId),
    [screensQuery.data, playlistId],
  )

  // Add and remove change the timeline in the cache first and let the server catch up, so a
  // row appears or goes the moment it is pressed. Each round trip is several queries to a
  // database a region away; waiting on it is what made moving an advert feel slow. A refusal
  // restores the snapshot, so the timeline never keeps something the server did not accept.
  const snapshot = async () => {
    await queryClient.cancelQueries({ queryKey: playlistKey })
    return queryClient.getQueryData<Playlist>(playlistKey)
  }
  const restore = (previous: Playlist | undefined) => { if (previous) queryClient.setQueryData(playlistKey, previous) }

  // Removing a booked advert ends its run on this screen only (the server unplaces that
  // booking target, so the repair loop does not put it back), and the library -- which reads
  // bookings from ['content'] -- refreshes with it.
  const removeMutation = useMutation({
    mutationFn: (itemId: number) => api.removePlaylistItem(playlistId, itemId),
    onMutate: async (itemId) => {
      const previous = await snapshot()
      if (previous) queryClient.setQueryData<Playlist>(playlistKey, { ...previous, items: previous.items.filter((item) => item.id !== itemId) })
      return { previous }
    },
    onError: (error: Error, _itemId, context) => { restore(context?.previous); toast.error(error.message) },
    onSuccess: () => toast.success(screenId ? 'Removed from this screen' : 'Item removed'),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: playlistKey })
      queryClient.invalidateQueries({ queryKey: ['content'] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
    },
  })

  // A booked advert joins this screen on the booking it already has: same client, same paid
  // window, no new sale, so there is nothing to ask. The server still guards what matters --
  // the plan's screen count, and never the same advert twice in one loop.
  const addBookedMutation = useMutation({
    mutationFn: (content: ContentItem) => api.addPlacementTarget(content.placement_id as number, { screen_id: screenId as number }),
    onMutate: async (content) => {
      const previous = await snapshot()
      if (previous) {
        const order = previous.items.reduce((max, item) => Math.max(max, item.order), -1) + 1
        const pending: PlaylistItem = {
          id: -content.id,
          content_id: content.id,
          content,
          duration: content.duration_ms ? Math.max(1, Math.round(content.duration_ms / 1000)) : 10,
          rotation: null,
          order,
          start_at: content.placement_starts_at ?? null,
          end_at: content.placement_ends_at ?? null,
          transition: null,
          transition_ms: null,
          schedule: null,
        }
        queryClient.setQueryData<Playlist>(playlistKey, { ...previous, items: [...previous.items, pending] })
      }
      return { previous }
    },
    onError: (error: Error, content, context) => {
      restore(context?.previous)
      toast.error(error.message)
      // The plan sold fewer screens than this would make. Changing the plan is the way
      // forward, and the booking dialog is where that happens.
      if (/plan|sold \d+ screen/i.test(error.message)) setBookingFor(content)
    },
    onSuccess: () => toast.success('Added to this screen'),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: playlistKey })
      queryClient.invalidateQueries({ queryKey: ['content'] })
      queryClient.invalidateQueries({ queryKey: ['screens'] })
    },
  })

  // Adding media directly to this playlist when commercial booking features are not active
  const addDirectMutation = useMutation({
    mutationFn: (content: ContentItem) => {
      const order = items.reduce((max, item) => Math.max(max, item.order), -1) + 1
      return api.addPlaylistItem(playlistId, content.id, order)
    },
    onMutate: async (content) => {
      const previous = await snapshot()
      if (previous) {
        const order = previous.items.reduce((max, item) => Math.max(max, item.order), -1) + 1
        const pending: PlaylistItem = {
          id: -content.id,
          content_id: content.id,
          content,
          duration: content.duration_ms ? Math.max(1, Math.round(content.duration_ms / 1000)) : 10,
          rotation: null,
          order,
          start_at: null,
          end_at: null,
          transition: null,
          transition_ms: null,
          schedule: null,
        }
        queryClient.setQueryData<Playlist>(playlistKey, { ...previous, items: [...previous.items, pending] })
      }
      return { previous }
    },
    onError: (error: Error, _content, context) => {
      restore(context?.previous)
      toast.error(error.message)
    },
    onSuccess: () => toast.success('Added to loop'),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: playlistKey })
      queryClient.invalidateQueries({ queryKey: ['content'] })
    },
  })

  // Zero-lag instant optimistic update for editing item duration, rotation, schedule, and transition
  const updateMutation = useMutation({
    mutationFn: ({ itemId, data }: { itemId: number; data: Parameters<typeof api.updatePlaylistItem>[2] }) =>
      api.updatePlaylistItem(playlistId, itemId, data),
    onMutate: async ({ itemId, data }) => {
      setSavingItem(itemId)
      const previous = await snapshot()
      if (previous) {
        queryClient.setQueryData<Playlist>(playlistKey, {
          ...previous,
          items: previous.items.map((it) => {
            if (it.id !== itemId) return it
            return {
              ...it,
              duration: data.duration !== undefined ? data.duration : it.duration,
              rotation: data.rotation !== undefined ? data.rotation : it.rotation,
              start_at: data.start_at !== undefined ? data.start_at : it.start_at,
              end_at: data.end_at !== undefined ? data.end_at : it.end_at,
              schedule: data.schedule !== undefined ? data.schedule : it.schedule,
              transition: data.transition !== undefined ? data.transition : it.transition,
              transition_ms: data.transition_ms !== undefined ? data.transition_ms : it.transition_ms,
            }
          }),
        })
      }
      return { previous }
    },
    onError: (error: Error, _vars, context) => {
      restore(context?.previous)
      toast.error(error.message)
    },
    onSuccess: (updated) => {
      if (updated) {
        queryClient.setQueryData<Playlist>(playlistKey, (old) => {
          if (!old) return old
          return {
            ...old,
            items: old.items.map((it) => (it.id === updated.id ? { ...it, ...updated } : it)),
          }
        })
      }
      toast.success('Item settings saved')
    },
    onSettled: () => setSavingItem(null),
  })

  // Zero-lag instant optimistic update for default playlist transitions
  const transitionMutation = useMutation({
    mutationFn: ({ transition, durationMs, applyToAll }: { transition: TransitionName; durationMs: number; applyToAll: boolean }) =>
      api.updatePlaylistTransitions(playlistId, { transition, transition_ms: durationMs, apply_to_all: applyToAll }),
    onMutate: async ({ transition, durationMs, applyToAll }) => {
      const previous = await snapshot()
      if (previous) {
        queryClient.setQueryData<Playlist>(playlistKey, {
          ...previous,
          default_transition: transition,
          default_transition_ms: durationMs,
          items: applyToAll
            ? previous.items.map((it) => ({
                ...it,
                transition,
                transition_ms: durationMs,
              }))
            : previous.items,
        })
      }
      return { previous }
    },
    onError: (error: Error, _vars, context) => {
      restore(context?.previous)
      toast.error(error.message)
    },
    onSuccess: (updatedPlaylist, variables) => {
      if (updatedPlaylist) {
        queryClient.setQueryData<Playlist>(playlistKey, updatedPlaylist)
      } else {
        queryClient.invalidateQueries({ queryKey: playlistKey })
      }
      toast.success(variables.applyToAll ? 'Transition applied to every item' : 'Playlist default saved')
    },
  })

  // The player has no notion of "the same advert", so a second copy in one loop is simply
  // the ad airing twice a cycle -- billed once, delivered twice. The server refuses it; the
  // library marks the advert as already here instead of offering something that can only fail.
  const placedContentIds = useMemo(() => new Set(items.map((item) => item.content.id)), [items])

  // Every advert, in two groups. Booked = its latest booking has not finished, including one
  // that starts later; the moment that window closes it reads as Not booked, with no job to
  // move it. The fetch time stands in for "now" so rendering stays pure, and it advances on
  // every refetch.
  // Items already placed in the loop are filtered out so they disappear when added and return on removal.
  const fetchedAt = contentQuery.dataUpdatedAt
  const library = useMemo(() => {
    const term = search.toLowerCase()
    const matches = (contentQuery.data || []).filter(
      (item) =>
        !placedContentIds.has(item.id) &&
        (item.name.toLowerCase().includes(term) || item.tags?.toLowerCase().includes(term)),
    )
    const isBooked = (item: ContentItem) =>
      Boolean(item.placement_id && item.placement_ends_at && new Date(item.placement_ends_at).getTime() > fetchedAt)
    return { booked: matches.filter(isBooked), notBooked: matches.filter((item) => !isBooked(item)) }
  }, [contentQuery.data, search, fetchedAt, placedContentIds])

  if (playlistQuery.isError || contentQuery.isError) return <ErrorState message="The playlist builder could not be loaded." onRetry={() => { playlistQuery.refetch(); contentQuery.refetch() }} />
  if (playlistQuery.isLoading) return <div className="space-y-6"><Skeleton className="h-24" /><div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_340px]"><Skeleton className="h-[520px]" /><Skeleton className="h-[520px]" /></div></div>
  const playlist = playlistQuery.data
  if (!playlist) return null
  const totalDuration = items.reduce((total, item) => total + item.duration, 0)

  const handleAddItem = (content: ContentItem, instant: boolean) => {
    const placed = placedContentIds.has(content.id)
    if (placed) {
      const existing = items.find((it) => it.content.id === content.id)
      if (existing) {
        setHighlightedItemId(existing.id)
        const el = document.getElementById(`timeline-item-${existing.id}`)
        el?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
        setTimeout(() => setHighlightedItemId((prev) => (prev === existing.id ? null : prev)), 2000)
        toast.info(`"${content.name}" is already in this loop`)
      }
      return
    }

    if (!hasCommercial) {
      addDirectMutation.mutate(content)
      return
    }

    if (instant) {
      addBookedMutation.mutate(content)
    } else {
      setBookingFor(content)
    }
  }

  const handleDragStart = ({ active }: DragStartEvent) => {
    const isTimeline = typeof active.id === 'number'
    setIsDraggingTimelineItem(isTimeline)
    const dragged = active.data.current?.content as ContentItem | undefined
    setDragging(dragged ?? items.find((item) => item.id === active.id)?.content ?? null)
  }

  const handleDragEnd = ({ active, over }: DragEndEvent) => {
    setDragging(null)
    setIsDraggingTimelineItem(false)
    if (!over) return

    // Dropping a timeline item onto the Content Library returns it to library (removes from loop)
    if (over.id === 'content-library-drop' && typeof active.id === 'number') {
      const itemToRemove = items.find((it) => it.id === active.id)
      if (itemToRemove) {
        removeMutation.mutate(active.id)
        toast.success(`Removed "${itemToRemove.content.name}" from loop`)
      }
      return
    }

    // A card from the library: same path as the + button, so placedContentIds, `instant`
    // and the booking dialog all behave identically. Dropped items append; reorder already
    // exists for positioning them.
    if (typeof active.id === 'string' && active.id.startsWith('lib-')) {
      const { content, instant } = active.data.current as { content: ContentItem; instant: boolean }
      handleAddItem(content, instant)
      return
    }

    // Timeline reorder
    if (active.id === over.id) return
    const oldIndex = items.findIndex((item) => item.id === active.id)
    let newIndex = items.findIndex((item) => item.id === over.id)
    if (newIndex === -1 && over.id === 'timeline') {
      newIndex = items.length - 1
    }
    if (oldIndex === -1 || newIndex === -1 || oldIndex === newIndex) return

    const order = arrayMove(items, oldIndex, newIndex).map((item) => item.id)
    setLocalOrder(order)
    debouncedReorder(order)
  }

  const handleMoveItem = (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1
    if (targetIndex < 0 || targetIndex >= items.length) return
    const newItems = arrayMove(items, index, targetIndex)
    const order = newItems.map((item) => item.id)
    setLocalOrder(order)
    debouncedReorder(order)
  }

  const renderLibraryContent = (isMobile = false) => (
    <>
      {!isMobile && (
        <div>
          <h2 id="library-title" className="font-semibold text-foreground">Content library</h2>
          <p className="mt-1 text-xs text-muted-foreground/70">
            {library.booked.length + library.notBooked.length} ads
            {screenId ? (hasCommercial ? ' · booked ones add instantly' : ' · click + to add to loop') : ''}
          </p>
        </div>
      )}
      <div className={cn('relative', !isMobile ? 'mt-3' : 'mt-1')}>
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground/70" />
        <Input
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search assets…"
          className="pl-9"
          aria-label="Search library"
        />
      </div>
      <div className={cn(
        'mt-3 space-y-4 overflow-y-auto pr-1',
        isMobile ? 'max-h-[55vh]' : 'max-h-[min(60vh,660px)] lg:max-h-[calc(100vh-17rem)]'
      )}>
        {contentQuery.isLoading ? (
          Array.from({ length: 4 }).map((_, index) => <Skeleton key={index} className="h-16 rounded-xl" />)
        ) : !hasCommercial ? (
          <div>
            <p className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Available Media <span className="font-normal normal-case">{library.booked.length + library.notBooked.length}</span>
            </p>
            <div className="space-y-2">
              {[...library.booked, ...library.notBooked].map((content) => (
                <LibraryRow
                  key={content.id}
                  content={content}
                  booked={false}
                  placed={placedContentIds.has(content.id)}
                  instant={true}
                  adding={addDirectMutation.isPending && addDirectMutation.variables?.id === content.id}
                  canEdit={canEdit}
                  fetchedAt={fetchedAt}
                  onAdd={() => handleAddItem(content, true)}
                />
              ))}
            </div>
            {!(library.booked.length + library.notBooked.length) && (
              <p className="rounded-xl border border-dashed border-hairline p-3 text-xs text-muted-foreground/70">
                {placedContentIds.size > 0 ? 'All uploaded media is in this loop.' : 'No media uploaded yet. Add media in Content library.'}
              </p>
            )}
          </div>
        ) : (
          <>
            <div>
              <p className="mb-2 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Booked <span className="font-normal normal-case">{library.booked.length}</span>
              </p>
              <div className="space-y-2">
                {library.booked.map((content) => (
                  <LibraryRow
                    key={content.id}
                    content={content}
                    booked
                    placed={placedContentIds.has(content.id)}
                    instant={Boolean(screenId)}
                    adding={addBookedMutation.isPending && addBookedMutation.variables?.id === content.id}
                    canEdit={canEdit}
                    fetchedAt={fetchedAt}
                    onAdd={() => handleAddItem(content, Boolean(screenId))}
                  />
                ))}
              </div>
              {!library.booked.length && (
                <p className="rounded-xl border border-dashed border-hairline p-3 text-xs text-muted-foreground/70">
                  {placedContentIds.size > 0 ? 'All booked ads are in this loop.' : 'No ad has a running booking.'}
                </p>
              )}
            </div>
            <div>
              <p className="mb-1 flex items-center justify-between text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Not booked <span className="font-normal normal-case">{library.notBooked.length}</span>
              </p>
              <p className="mb-2 text-[11px] text-muted-foreground/70">Adding one asks for a plan or a custom booking.</p>
              <div className="space-y-2">
                {library.notBooked.map((content) => (
                  <LibraryRow
                    key={content.id}
                    content={content}
                    booked={false}
                    placed={placedContentIds.has(content.id)}
                    instant={false}
                    adding={addBookedMutation.isPending && addBookedMutation.variables?.id === content.id}
                    canEdit={canEdit}
                    fetchedAt={fetchedAt}
                    onAdd={() => handleAddItem(content, false)}
                  />
                ))}
              </div>
              {!library.notBooked.length && (
                <p className="rounded-xl border border-dashed border-hairline p-3 text-xs text-muted-foreground/70">
                  {placedContentIds.size > 0 ? 'No other ads in library.' : 'Every ad is booked.'}
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </>
  )

  return (
    <div className="relative space-y-8">
      {showHeader && <PageHeader eyebrow="Playlist builder" title={playlist.name} description="Set the loop order, control each handoff, and schedule exactly when every item may play." actions={<div className="flex items-center gap-2"><Badge variant="outline">{items.length} items</Badge><Badge variant="outline">{loopDuration(totalDuration)} loop</Badge>{!canEdit && <Badge variant="warning">View only</Badge>}</div>} />}

      <DndContext
        sensors={sensors}
        collisionDetection={collisionDetection}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <section aria-labelledby="timeline-title" className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 id="timeline-title" className="text-sm font-semibold text-foreground">Playback timeline</h2>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setMobileLibraryOpen(true)}
              className="lg:hidden h-8 text-xs font-medium gap-1.5"
            >
              <Plus className="size-3.5" /> Add from library
            </Button>
          </div>

          {hasTransitions && (
            <DefaultTransitionPanel key={`${playlist.default_transition}-${playlist.default_transition_ms}`} playlist={playlist} disabled={!canEdit} pending={transitionMutation.isPending} onSave={(transition, durationMs, applyToAll) => transitionMutation.mutate({ transition, durationMs, applyToAll })} />
          )}

          <TimelineDropZone>
          {!items.length ? (
            <div className="space-y-3">
              <EmptyState icon={ListVideo} title="This playlist has no content" description="Drag an asset in from the library, or press its + button." />
              <div className="flex justify-center lg:hidden">
                <Button onClick={() => setMobileLibraryOpen(true)} className="gap-2">
                  <Plus className="size-4" /> Browse library to add
                </Button>
              </div>
            </div>
          ) : (
              <SortableContext items={items.map((item) => item.id)} strategy={verticalListSortingStrategy}>
                <div className="space-y-3">
                  {items.map((item, index) => (
                    <MemoizedItemRow
                      key={item.id}
                      item={item}
                      index={index}
                      totalItems={items.length}
                      defaultTransition={playlist.default_transition}
                      defaultDurationMs={playlist.default_transition_ms}
                      hasTransitions={hasTransitions}
                      hasCommercial={hasCommercial}
                      canEdit={canEdit && item.id > 0}
                      saving={savingItem === item.id}
                      highlighted={highlightedItemId === item.id}
                      onMoveUp={() => handleMoveItem(index, 'up')}
                      onMoveDown={() => handleMoveItem(index, 'down')}
                      onRemove={() => removeMutation.mutate(item.id)}
                      onSave={(data) => updateMutation.mutate({ itemId: item.id, data })}
                    />
                  ))}
                </div>
              </SortableContext>
          )}
          </TimelineDropZone>

          {/* Bottom mobile banner for rapid access without scrolling */}
          <div className="lg:hidden pt-2">
            <Button
              variant="outline"
              onClick={() => setMobileLibraryOpen(true)}
              className="w-full h-11 border-dashed border-primary/40 bg-primary/5 text-primary hover:bg-primary/10 gap-2 font-medium"
            >
              <Plus className="size-4" /> Add ad from Content Library ({library.booked.length + library.notBooked.length} available)
            </Button>
          </div>
        </section>

        {/* Desktop Sticky Sidebar */}
        <div className="hidden lg:block">
          <LibraryDropZone isDraggingTimelineItem={isDraggingTimelineItem}>
            {renderLibraryContent(false)}
          </LibraryDropZone>
        </div>
      </div>

        {/* Mobile Slide-Up Bottom Sheet */}
        <MobileLibrarySheet
          open={mobileLibraryOpen}
          onOpenChange={setMobileLibraryOpen}
          subtitle={`${library.booked.length + library.notBooked.length} ads available${screenId ? (hasCommercial ? ' · booked ones add instantly' : ' · click + to add to loop') : ''}`}
        >
          {renderLibraryContent(true)}
        </MobileLibrarySheet>

        {/* ONE overlay, with content for BOTH drag kinds. SortableContext derives
            useDragOverlay from whether the overlay has measured a rect, and DragOverlay
            mounts whenever a drag is active regardless of its children -- so rendering
            content for library drags only would stop the reordered row following the
            cursor, with no visible error.
            Keep it as a direct child of DndContext: it is position:fixed, not portaled, so
            any ancestor with transform/filter/backdrop-filter would clip it. */}
        <DragOverlay dropAnimation={{ duration: 180, easing: 'cubic-bezier(0.18, 0.67, 0.6, 1.22)' }}>
          {dragging ? (
            <div className="flex items-center gap-3 rounded-xl border border-primary/40 bg-card/95 p-3 shadow-2xl backdrop-blur-md ring-2 ring-primary/20 max-w-sm pointer-events-none select-none">
              <MediaThumbnail item={dragging} className="size-12 shrink-0 rounded-lg shadow-sm" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-semibold text-foreground">{dragging.name}</p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  {clipDuration(dragging.duration_ms) || `${loopDuration(10)}`}
                </p>
              </div>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {/* A new booking, or a plan change when a booked advert has run out of screens. */}
      {hasCommercial && bookingFor && (
        <BookOrExtendDialog
          content={bookingFor}
          screens={screensShowingThisPlaylist}
          open={Boolean(bookingFor)}
          onOpenChange={(next) => { if (!next) setBookingFor(null) }}
        />
      )}
    </div>
  )
}
