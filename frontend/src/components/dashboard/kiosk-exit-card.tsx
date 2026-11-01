'use client'

import { ChevronDown, KeyRound, Hand, Gamepad2 } from 'lucide-react'
import type { Screen } from '@/lib/types'

/**
 * How to get out of kiosk on this screen, and the code that does it.
 *
 * A provisioned panel swallows Home and Back — that is the point of kiosk, and it is also how
 * an installer ends up stranded in front of a television with no way into settings. The way
 * out already existed; nothing told anyone what it was, so the only move people found was a
 * factory reset, which unpairs the screen and loses its playlist.
 *
 * The PIN is shown rather than hidden. Secrecy is not what protects a screen here — physical
 * access to it is — and a code the operator cannot see is a code that ends up on a sticky
 * note taped to the bezel.
 */
export function KioskExitCard({ screen }: { screen: Screen }) {
  const pin = screen.effective_maintenance_pin || screen.maintenance_pin
  // The effective code differs from this screen's own only when a workspace PIN is set
  // and this screen has not opted out of it (Settings > "Keep this screen on its own pin").
  const isOwnPin = !screen.effective_maintenance_pin || screen.effective_maintenance_pin === screen.maintenance_pin

  return (
    <details className="ring-hairline bg-card group rounded-2xl ring-1 [&[open]_.chevron]:rotate-180">
      {/* Collapsed by default, but the PIN stays on the summary: the person who needs this
          is standing in front of a locked television, and the code is the only part of it
          they actually need in that moment. */}
      <summary className="flex cursor-pointer list-none items-center gap-2.5 p-5 font-semibold sm:p-6 [&::-webkit-details-marker]:hidden">
        <span className="bg-primary/10 text-primary dark:text-brand hidden size-9 shrink-0 place-items-center rounded-lg sm:grid">
          <KeyRound className="size-4" aria-hidden="true" />
        </span>
        <span className="text-foreground min-w-0 flex-1 truncate">Getting into this screen</span>
        {pin ? (
          <code className="bg-primary/10 text-primary dark:text-brand shrink-0 rounded-lg px-2.5 py-1 text-sm font-semibold tracking-[0.2em]">
            {pin}
          </code>
        ) : (
          <span className="shrink-0 text-sm font-medium text-amber-600 dark:text-amber-400">No PIN</span>
        )}
        <ChevronDown className="chevron text-muted-foreground size-4 shrink-0 transition-transform" aria-hidden="true" />
      </summary>

      <div className="px-5 pb-5 sm:px-6 sm:pb-6">
      <p className="text-muted-foreground text-sm">
            A signage screen blocks Home and Back on purpose, so an advert cannot be closed by
        a passer-by. Use one of these to reach settings.
      </p>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <div className="bg-secondary/40 rounded-xl p-3.5">
          <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
            <Hand className="size-4" aria-hidden="true" /> Touch screen
          </p>
          <p className="text-muted-foreground mt-1 text-sm">
            Tap any <span className="text-foreground">corner 7 times</span> within 3 seconds.
          </p>
        </div>
        <div className="bg-secondary/40 rounded-xl p-3.5">
          <p className="text-foreground flex items-center gap-1.5 text-sm font-medium">
            <Gamepad2 className="size-4" aria-hidden="true" /> With a remote
          </p>
          <p className="text-muted-foreground mt-1 text-sm">
            Press <span className="text-foreground">Up, Up, Down, Down, OK</span>.
          </p>
        </div>
      </div>

      <div className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-muted-foreground text-sm">Then enter the code above.</span>
        {!pin && (
          <span className="text-sm font-medium text-amber-600 dark:text-amber-400">
            No PIN is set — ask your platform administrator.
          </span>
        )}
        {pin && !isOwnPin && (
          <span className="text-muted-foreground/70 text-sm">
            (the workspace pin, set on the Screens page — this covers every screen that has
            not opted to keep its own)
          </span>
        )}
      </div>

      <p className="text-muted-foreground/70 mt-4 text-sm">
        Works with no internet: the screen remembers this code, which is exactly when you
        are most likely to need it. Set a different one for this screen under Settings.
      </p>
      </div>
    </details>
  )
}
