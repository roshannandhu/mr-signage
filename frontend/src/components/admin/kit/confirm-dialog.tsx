'use client'

import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

/**
 * Confirmation for admin actions, in three levels:
 *   1 reversible  -- a plain confirm (extend, toggle)
 *   2 impactful   -- shows the impact and requires a reason, which the audit log stores
 *   3 irreversible -- also requires typing `confirmText` (a name) exactly
 *
 * The server enforces its own guards; this is the courtesy that stops a mis-click.
 */
export type ConfirmOptions = {
  title: string
  description?: ReactNode
  impact?: ReactNode
  confirmLabel?: string
  destructive?: boolean
  level?: 1 | 2 | 3
  confirmText?: string
}

export type ConfirmResult = { reason?: string } | null

type Pending = ConfirmOptions & { resolve: (result: ConfirmResult) => void }

const ConfirmContext = createContext<((options: ConfirmOptions) => Promise<ConfirmResult>) | null>(null)

export function useConfirm() {
  const confirm = useContext(ConfirmContext)
  if (!confirm) throw new Error('useConfirm must be used inside <ConfirmProvider>')
  return confirm
}

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)
  const [reason, setReason] = useState('')
  const [typed, setTyped] = useState('')
  const pendingRef = useRef<Pending | null>(null)

  const confirm = useCallback((options: ConfirmOptions) => {
    return new Promise<ConfirmResult>((resolve) => {
      const next = { ...options, resolve }
      pendingRef.current = next
      setReason('')
      setTyped('')
      setPending(next)
    })
  }, [])

  const settle = (result: ConfirmResult) => {
    pendingRef.current?.resolve(result)
    pendingRef.current = null
    setPending(null)
  }

  const level = pending?.level ?? 1
  const needsReason = level >= 2
  const needsTyped = level === 3 && Boolean(pending?.confirmText)
  const ready =
    (!needsReason || reason.trim().length >= 3) &&
    (!needsTyped || typed.trim() === pending?.confirmText)

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <Dialog open={pending !== null} onOpenChange={(open) => { if (!open) settle(null) }}>
        {pending && (
          <DialogContent className="sm:max-w-md">
            <form
              className="grid gap-4"
              onSubmit={(event) => {
                event.preventDefault()
                if (ready) settle({ reason: reason.trim() || undefined })
              }}
            >
              <DialogHeader>
                {/* pr-8 keeps a long title clear of the dialog's close button. */}
                <DialogTitle className="flex items-start gap-2 pr-8 leading-snug">
                  {pending.destructive && <AlertTriangle className="size-4 text-red-700 dark:text-red-400" aria-hidden="true" />}
                  {pending.title}
                </DialogTitle>
                {pending.description && <DialogDescription>{pending.description}</DialogDescription>}
              </DialogHeader>

              {pending.impact && (
                <div className="rounded-lg border border-border bg-muted px-3 py-2 text-sm text-foreground">
                  {pending.impact}
                </div>
              )}

              {needsReason && (
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm-reason">Reason (saved to the audit log)</Label>
                  <Input
                    id="confirm-reason"
                    autoFocus
                    value={reason}
                    maxLength={500}
                    onChange={(event) => setReason(event.target.value)}
                    placeholder="Why is this being done?"
                  />
                </div>
              )}

              {needsTyped && (
                <div className="grid gap-1.5">
                  <Label htmlFor="confirm-typed">
                    Type <span className="font-mono font-semibold text-foreground">{pending.confirmText}</span> to confirm
                  </Label>
                  <Input
                    id="confirm-typed"
                    value={typed}
                    autoComplete="off"
                    onChange={(event) => setTyped(event.target.value)}
                  />
                </div>
              )}

              <DialogFooter>
                <Button type="button" variant="outline" onClick={() => settle(null)}>Cancel</Button>
                <Button type="submit" variant={pending.destructive ? 'destructive' : 'default'} disabled={!ready}>
                  {pending.confirmLabel ?? 'Confirm'}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        )}
      </Dialog>
    </ConfirmContext.Provider>
  )
}
