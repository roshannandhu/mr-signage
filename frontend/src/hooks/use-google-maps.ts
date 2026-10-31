'use client'

import { useEffect, useSyncExternalStore } from 'react'

export const MAPS_KEY = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY || ''

type Status = 'disabled' | 'loading' | 'ready' | 'error'

type MapsWindow = Window & {
  google?: { maps?: { Map?: unknown } }
  gm_authFailure?: () => void
  [READY_CALLBACK]?: () => void
}

// The SDK loads once per page no matter how many maps mount, so its status is genuinely
// module state, not component state — a second map joins the first load rather than
// injecting the script again, which logs a console error and can leave the later caller
// with a half-initialised namespace. Components subscribe to it as an external store.
const SCRIPT_ID = 'google-maps-sdk'
const READY_CALLBACK = '__mrMapsReady'
let sdkStatus: Status = MAPS_KEY ? 'loading' : 'disabled'
let loader: Promise<void> | null = null
const listeners = new Set<() => void>()

function publish(next: Status) {
  if (sdkStatus === next) return
  sdkStatus = next
  listeners.forEach((notify) => notify())
}

function subscribe(callback: () => void) {
  listeners.add(callback)
  return () => { listeners.delete(callback) }
}

const snapshot = () => sdkStatus
const serverSnapshot = (): Status => (MAPS_KEY ? 'loading' : 'disabled')

function loadSdk(libraries: string): Promise<void> {
  if (loader) return loader
  loader = new Promise<void>((resolve, reject) => {
    if (typeof window === 'undefined') return reject(new Error('not a browser'))
    const win = window as MapsWindow

    // Google reports a rejected key — wrong referrer, API not enabled, billing not linked
    // — through this global, and it fires *after* the script loaded successfully. It can
    // therefore never be folded into this promise: by then every caller has resolved. Only
    // a store already-mounted maps can be pushed an update through catches it.
    win.gm_authFailure = () => publish('error')

    if (win.google?.maps?.Map) return resolve()

    // Google calls this only once the API *and* the requested libraries are usable. The
    // script's own onload is not that moment — under loading=async the namespace is still
    // filling in, which is why touching google.maps.Map there throws "not a constructor".
    win[READY_CALLBACK] = () => resolve()

    const script = document.createElement('script')
    script.id = SCRIPT_ID
    script.async = true
    script.src =
      `https://maps.googleapis.com/maps/api/js?key=${encodeURIComponent(MAPS_KEY)}` +
      `&libraries=${libraries}&loading=async&v=weekly&callback=${READY_CALLBACK}`
    script.onerror = () => reject(new Error('Google Maps failed to load'))
    document.head.appendChild(script)
  })

  loader
    // An auth failure can land before the libraries finish resolving; it outranks success.
    .then(() => { if (sdkStatus !== 'error') publish('ready') })
    .catch(() => publish('error'))

  return loader
}

/**
 * Loads the Google Maps JavaScript SDK once and reports where it got to.
 *
 * Returns `disabled` rather than throwing when no key is configured, so every caller can
 * render a sensible fallback instead of the page breaking for a workspace that has not
 * enabled billing yet.
 */
export function useGoogleMaps(libraries = 'marker,places'): Status {
  const status = useSyncExternalStore(subscribe, snapshot, serverSnapshot)

  useEffect(() => {
    if (MAPS_KEY) loadSdk(libraries)
  }, [libraries])

  return status
}
