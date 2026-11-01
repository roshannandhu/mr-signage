'use client'

import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { WS_BASE } from '@/lib/api'
import { useAuthStore } from '@/lib/store'

/**
 * Live fleet alerts, pushed rather than polled.
 *
 * The server has published to `dashboard:{org}` and served an authenticated socket at
 * /api/ws/dashboard/ws since P6, but nothing in the dashboard ever opened it — there is no
 * other `new WebSocket` in this codebase. So the whole realtime path existed and delivered
 * nothing, and the alerts page fell back to re-fetching the entire screen list every thirty
 * seconds. This connects the half that was missing.
 *
 * Polling stays as the fallback exactly as the spec requires: if the socket never opens, or
 * drops and cannot reconnect, the queries underneath still refresh on their own interval.
 * This makes alerts *faster*, never load-bearing.
 */
export function useFleetAlerts() {
  const token = useAuthStore((state) => state.token)
  // A browser WebSocket cannot carry the X-Act-As-Org header the HTTP API uses, so the
  // workspace a platform operator stepped into goes on the URL instead. Without it the
  // socket subscribed to the operator's own organisation's channel, and alert toasts plus
  // live screen/playlist updates never arrived while acting inside a tenant's workspace.
  const actingId = useAuthStore((state) => state.acting?.id ?? null)
  const queryClient = useQueryClient()
  // Held in a ref so reconnect scheduling survives re-renders without restarting the
  // backoff, and so the cleanup below always closes the socket it actually opened.
  const socketRef = useRef<WebSocket | null>(null)
  const attemptRef = useRef(0)
  const closedByUsRef = useRef(false)

  useEffect(() => {
    if (!token) return
    closedByUsRef.current = false
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined

    const connect = () => {
      if (closedByUsRef.current) return
      let socket: WebSocket
      try {
        const actAsParam = actingId ? `&act_as=${actingId}` : ''
        socket = new WebSocket(`${WS_BASE}/ws/dashboard/ws?token=${encodeURIComponent(token)}${actAsParam}`)
      } catch {
        // A malformed URL should not take the dashboard down; polling still works.
        return
      }
      socketRef.current = socket

      socket.onopen = () => {
        attemptRef.current = 0
      }

      socket.onmessage = (event) => {
        let payload: { type?: string; alert?: { id: number; severity: string; title: string; detail?: string | null } }
        try {
          payload = JSON.parse(event.data)
        } catch {
          return
        }

        // Any fleet event can change what the alert views show, so refresh them rather than
        // trying to patch the cache from the message. The payload is a notification, not a
        // source of truth — the API stays authoritative.
        if (payload.type === 'alert_raised' || payload.type === 'alert_resolved') {
          queryClient.invalidateQueries({ queryKey: ['alerts'] })
          queryClient.invalidateQueries({ queryKey: ['alert-summary'] })
        }
        if (payload.type === 'screen_update' || payload.type === 'alert_resolved') {
          queryClient.invalidateQueries({ queryKey: ['screens'] })
        }

        // Only raises are announced. Toasting a resolution too would mean an operator who
        // steps away returns to a stack of "X is offline" / "X is back" pairs cancelling
        // each other out, which is noise pretending to be information.
        if (payload.type === 'alert_raised' && payload.alert) {
          const { title, detail, severity } = payload.alert
          const show = severity === 'critical' ? toast.error : toast.warning
          show(title, { description: detail ?? undefined, duration: severity === 'critical' ? 12_000 : 6_000 })
        }
      }

      socket.onclose = () => {
        socketRef.current = null
        if (closedByUsRef.current) return
        // Exponential backoff to 30s. A dashboard left open overnight against a backend
        // that is down must not reconnect in a tight loop.
        const delay = Math.min(1000 * 2 ** attemptRef.current, 30_000)
        attemptRef.current += 1
        reconnectTimer = setTimeout(connect, delay)
      }

      socket.onerror = () => {
        // onclose always follows, and owns the retry.
        socket.close()
      }
    }

    connect()

    return () => {
      closedByUsRef.current = true
      if (reconnectTimer) clearTimeout(reconnectTimer)
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [token, actingId, queryClient])
}
