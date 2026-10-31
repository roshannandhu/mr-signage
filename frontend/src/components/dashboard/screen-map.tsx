'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Crosshair, Maximize2, MapPin, Minimize2 } from 'lucide-react'
import { useTheme } from 'next-themes'
import { Badge } from '@/components/ui/badge'
import { useGoogleMaps } from '@/hooks/use-google-maps'

// OpenStreetMap standard tiles — 100% free, universal, zero API keys or authentication required.
const TILES = {
  dark: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  light: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
}
const ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
/** Pan beyond this and the map has stopped showing what it was opened to show. */
const STRAYED_METRES = 400

export type MapPoint = {
  id: number
  name: string
  location?: string | null
  latitude?: number | null
  longitude?: number | null
  online?: boolean
  /** Optional line under the name in the popup, e.g. "5,210 plays". */
  detail?: string
}

/**
 * Several places on one map, drawn with Leaflet over Carto's keyless basemap.
 *
 * Handles one pin as happily as fifty. A single place used to render Google's keyless
 * embed instead, but Google now 301s that to an endpoint answering 404 with
 * X-Frame-Options: SAMEORIGIN, so it was a permanently grey box.
 */
function LeafletMap({ points, height }: { points: MapPoint[]; height: number }) {
  const container = useRef<HTMLDivElement>(null)
  const shell = useRef<HTMLDivElement>(null)
  const map = useRef<import('leaflet').Map | null>(null)
  const tiles = useRef<import('leaflet').TileLayer | null>(null)
  // Where the map was framed when it opened, so "recentre" has something to mean.
  const home = useRef<{ centre: [number, number]; zoom: number } | null>(null)

  const { resolvedTheme } = useTheme()
  const [strayed, setStrayed] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)

  // Keyed on the pins' values rather than the array identity, so a parent re-render that
  // produces an equal list does not tear the map down and undo the user's pan.
  const key = JSON.stringify(points.map((p) => [p.id, p.latitude, p.longitude, p.online, p.detail]))

  useEffect(() => {
    let cancelled = false
    let created: import('leaflet').Map | null = null

    // Imported here rather than at module scope: Leaflet touches window on import, which
    // breaks the server render of every page that shows a map.
    import('leaflet').then((L) => {
      if (cancelled || !container.current || map.current) return

      created = L.map(container.current, {
        // Off by default so the page still scrolls when the cursor crosses the map. It is
        // switched on in fullscreen below, where the map IS the page.
        scrollWheelZoom: false,
        attributionControl: true,
        zoomAnimation: true,
        fadeAnimation: true,
      })
      map.current = created
      tiles.current = L.tileLayer(TILES[resolvedTheme === 'light' ? 'light' : 'dark'], {
        maxZoom: 20,
        attribution: ATTRIBUTION,
      }).addTo(created)

      const bounds: [number, number][] = []
      for (const point of points) {
        if (point.latitude == null || point.longitude == null) continue
        const colour = point.online ? '#16a34a' : '#64748b'
        const pulse = point.online
          ? '<span class="mr-pin-pulse" style="position:absolute;inset:0;border-radius:9999px;background:' +
            colour +
            '"></span>'
          : ''
        const marker = L.marker([point.latitude, point.longitude], {
          title: point.name,
          icon: L.divIcon({
            className: '',
            html:
              '<div style="position:relative;width:18px;height:18px">' +
              pulse +
              '<span style="position:absolute;inset:0;border-radius:9999px;background:' +
              colour +
              ';border:3px solid #fff;box-shadow:0 1px 6px rgba(0,0,0,.55)"></span></div>',
            iconSize: [18, 18],
            iconAnchor: [9, 9],
          }),
        }).addTo(created)

        const coords = point.latitude + ',' + point.longitude
        marker.bindPopup(
          '<div style="font:500 13px system-ui;min-width:150px">' +
            '<div style="font-weight:600">' +
            point.name +
            '</div>' +
            (point.location ? '<div style="opacity:.7">' + point.location + '</div>' : '') +
            (point.detail ? '<div style="margin-top:3px">' + point.detail + '</div>' : '') +
            '<div style="margin-top:3px;color:' +
            colour +
            '">' +
            (point.online ? 'Online' : 'Offline') +
            '</div>' +
            '<a href="https://www.google.com/maps/search/?api=1&query=' +
            encodeURIComponent(coords) +
            '" target="_blank" rel="noreferrer" style="display:inline-block;margin-top:5px">Open in Google Maps</a>' +
            '</div>',
        )
        bounds.push([point.latitude, point.longitude])
      }

      if (bounds.length === 1) created.setView(bounds[0], 15)
      else if (bounds.length) created.fitBounds(bounds, { padding: [40, 40] })

      home.current = {
        centre: [created.getCenter().lat, created.getCenter().lng],
        zoom: created.getZoom(),
      }

      // Only offer "recentre" once the view has actually left the place it was framed to.
      // A button that is always lit is noise, and a nudge of a few metres is not straying.
      created.on('moveend zoomend', () => {
        const anchor = home.current
        const live = map.current
        if (!anchor || !live) return
        const away = live.distance(live.getCenter(), anchor.centre)
        // Tolerance, not equality: flyTo interpolates zoom continuously and lands on
        // 15.000000000000002, so a strict !== leaves the button lit forever after the
        // very click that was supposed to put it away.
        const zoomed = Math.abs(live.getZoom() - anchor.zoom) > 0.05
        setStrayed(away > STRAYED_METRES || zoomed)
      })
    })

    return () => {
      cancelled = true
      created?.remove()
      if (map.current === created) map.current = null
      tiles.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on values, not reference
  }, [key])

  // Swap the basemap in place when the dashboard theme flips. Rebuilding the map instead
  // would throw away whatever the operator had panned to.
  useEffect(() => {
    tiles.current?.setUrl(TILES[resolvedTheme === 'light' ? 'light' : 'dark'])
  }, [resolvedTheme])

  const recentre = useCallback(() => {
    const anchor = home.current
    // flyTo rather than setView: the arc shows the reader where they were dragged back
    // from, which an instant jump does not.
    if (anchor) map.current?.flyTo(anchor.centre, anchor.zoom, { duration: 0.9, easeLinearity: 0.22 })
  }, [])

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
    else void shell.current?.requestFullscreen?.().catch(() => {})
  }, [])

  useEffect(() => {
    const onChange = () => {
      const on = document.fullscreenElement === shell.current
      setFullscreen(on)
      const live = map.current
      if (!live) return
      // The wheel belongs to the map only when the map owns the screen.
      if (on) live.scrollWheelZoom.enable()
      else live.scrollWheelZoom.disable()
      // Leaflet caches the container size, so without this the tiles keep the old shape
      // and leave grey bands down the side of a full screen.
      setTimeout(() => live.invalidateSize(), 120)
    }
    document.addEventListener('fullscreenchange', onChange)
    return () => document.removeEventListener('fullscreenchange', onChange)
  }, [])

  return (
    <div
      ref={shell}
      className={`mr-map border-hairline bg-muted relative z-0 overflow-hidden rounded-xl border ${
        resolvedTheme === 'dark'
          ? '[&_.leaflet-tile-pane]:brightness-[0.7] [&_.leaflet-tile-pane]:invert-[1] [&_.leaflet-tile-pane]:hue-rotate-[180deg] [&_.leaflet-tile-pane]:contrast-[1.1]'
          : ''
      }`}
      style={{ height: fullscreen ? '100vh' : height }}
    >
      <div ref={container} className="size-full" />

      <div className="absolute top-2 right-2 z-[1000] flex flex-col gap-1.5">
        <button
          type="button"
          onClick={toggleFullscreen}
          aria-label={fullscreen ? 'Exit full screen' : 'View map full screen'}
          className="bg-background/85 text-foreground ring-hairline grid size-8 cursor-pointer place-items-center rounded-lg shadow-sm ring-1 backdrop-blur transition hover:scale-105 active:scale-95"
        >
          {fullscreen ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
        </button>
        <button
          type="button"
          onClick={recentre}
          aria-label="Recentre the map"
          aria-hidden={!strayed}
          // Kept mounted and faded rather than unmounted, so it does not shove the
          // fullscreen button around as it comes and goes.
          className={`bg-background/85 text-foreground ring-hairline grid size-8 place-items-center rounded-lg shadow-sm ring-1 backdrop-blur transition duration-300 hover:scale-105 active:scale-95 ${
            strayed ? 'cursor-pointer opacity-100' : 'pointer-events-none opacity-0'
          }`}
        >
          <Crosshair className="size-4" />
        </button>
      </div>
    </div>
  )
}

/**
 * The real Google map, drawn with the Maps JavaScript SDK.
 *
 * Used only once a key is configured. Google retired the keyless iframe embed -- it now
 * 301s to an endpoint answering 404 with X-Frame-Options: SAMEORIGIN -- so a genuine
 * Google map is a key-or-nothing choice, and LeafletMap covers the nothing.
 */
function GoogleMap({ points, height }: { points: MapPoint[]; height: number }) {
  const container = useRef<HTMLDivElement>(null)
  const shell = useRef<HTMLDivElement>(null)
  const map = useRef<google.maps.Map | null>(null)
  const home = useRef<{ centre: google.maps.LatLngLiteral; zoom: number } | null>(null)

  const [strayed, setStrayed] = useState(false)

  const key = JSON.stringify(points.map((p) => [p.id, p.latitude, p.longitude, p.online, p.detail]))

  useEffect(() => {
    if (!container.current || map.current) return
    const created = new google.maps.Map(container.current, {
      // Google's own full-screen control is better than anything hand-rolled: it keeps the
      // map's gesture handling and Street View intact.
      fullscreenControl: true,
      mapTypeControl: false,
      streetViewControl: true,
      zoomControl: true,
      // Otherwise a scroll down the page is swallowed the moment the cursor crosses a map.
      gestureHandling: 'cooperative',
      // No custom styles: Google's default cartography is exactly the familiar map an
      // operator expects, and restyling it only makes it look like something else.
    })
    map.current = created

    const bounds = new google.maps.LatLngBounds()
    const info = new google.maps.InfoWindow()
    let count = 0

    for (const point of points) {
      if (point.latitude == null || point.longitude == null) continue
      const position = { lat: point.latitude, lng: point.longitude }
      const colour = point.online ? '#16a34a' : '#64748b'
      const marker = new google.maps.Marker({
        map: created,
        position,
        title: point.name,
        animation: google.maps.Animation.DROP,
        icon: {
          path: google.maps.SymbolPath.CIRCLE,
          scale: 8,
          fillColor: colour,
          fillOpacity: 1,
          strokeColor: '#ffffff',
          strokeWeight: 3,
        },
      })
      const coords = point.latitude + ',' + point.longitude
      marker.addListener('click', () => {
        info.setContent(
          '<div style="font:500 13px system-ui;min-width:150px;color:#0f172a">' +
            '<div style="font-weight:600">' + point.name + '</div>' +
            (point.location ? '<div style="opacity:.7">' + point.location + '</div>' : '') +
            (point.detail ? '<div style="margin-top:3px">' + point.detail + '</div>' : '') +
            '<div style="margin-top:3px;color:' + colour + '">' +
            (point.online ? 'Online' : 'Offline') + '</div>' +
            '<a href="https://www.google.com/maps/search/?api=1&query=' +
            encodeURIComponent(coords) +
            '" target="_blank" rel="noreferrer" style="display:inline-block;margin-top:5px">Open in Google Maps</a>' +
            '</div>',
        )
        info.open({ map: created, anchor: marker })
      })
      bounds.extend(position)
      count += 1
    }

    if (count === 1) {
      created.setCenter(bounds.getCenter())
      created.setZoom(16)
    } else if (count > 1) {
      created.fitBounds(bounds, 48)
    }

    // Read the framing back once Google has settled it, so "recentre" restores what the
    // operator actually saw rather than what we asked for.
    google.maps.event.addListenerOnce(created, 'idle', () => {
      const centre = created.getCenter()
      if (centre) home.current = { centre: centre.toJSON(), zoom: created.getZoom() ?? 16 }
    })

    created.addListener('idle', () => {
      const anchor = home.current
      const centre = created.getCenter()
      if (!anchor || !centre) return
      // Degrees rather than metres: the geometry library is not among the ones loaded, and
      // ~0.004° is close enough to "you have panned away from the pin" at any city zoom.
      const drifted =
        Math.abs(centre.lat() - anchor.centre.lat) > 0.004 ||
        Math.abs(centre.lng() - anchor.centre.lng) > 0.004
      setStrayed(drifted || (created.getZoom() ?? anchor.zoom) !== anchor.zoom)
    })

    return () => {
      map.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- keyed on values, not reference
  }, [key])

  const recentre = useCallback(() => {
    const anchor = home.current
    if (!anchor || !map.current) return
    // panTo eases; setCenter snaps. The ease is what tells you where you were brought back from.
    map.current.panTo(anchor.centre)
    map.current.setZoom(anchor.zoom)
  }, [])

  return (
    <div
      ref={shell}
      className="mr-map border-hairline bg-muted relative z-0 overflow-hidden rounded-xl border"
      style={{ height }}
    >
      <div ref={container} className="size-full" />
      <button
        type="button"
        onClick={recentre}
        aria-label="Recentre the map"
        aria-hidden={!strayed}
        // Left side: Google owns the right-hand rail with its own controls.
        className={`bg-background/85 text-foreground ring-hairline absolute bottom-6 left-2 z-10 grid size-8 place-items-center rounded-lg shadow-sm ring-1 backdrop-blur transition duration-300 hover:scale-105 active:scale-95 ${
          strayed ? 'cursor-pointer opacity-100' : 'pointer-events-none opacity-0'
        }`}
      >
        <Crosshair className="size-4" />
      </button>
    </div>
  )
}

function NoLocations({ points }: { points: MapPoint[] }) {
  const named = [...new Set(points.map((p) => p.location?.trim()).filter(Boolean))] as string[]

  if (named.length > 0) {
    return (
      <div className="border-hairline bg-card/60 rounded-xl border p-5">
        <div className="flex flex-wrap items-center justify-between gap-2 pb-3 mb-3 border-b border-border/50">
          <div className="flex items-center gap-2">
            <span className="grid size-8 place-items-center rounded-lg bg-primary/10 text-primary">
              <MapPin className="size-4" />
            </span>
            <div>
              <p className="text-sm font-semibold text-foreground">
                {named.length} Venue Location{named.length === 1 ? '' : 's'} Active
              </p>
              <p className="text-xs text-muted-foreground">
                Assigned screens running this advert
              </p>
            </div>
          </div>
          <span className="text-xs text-muted-foreground bg-muted px-2.5 py-1 rounded-md">
            GPS pin optional
          </span>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
          {named.map((place) => {
            const screensAtPlace = points.filter((p) => p.location?.trim() === place)
            const onlineCount = screensAtPlace.filter((p) => p.online).length
            return (
              <div
                key={place}
                className="rounded-xl border border-hairline bg-background/80 p-3.5 flex flex-col justify-between gap-2.5 shadow-sm hover:border-primary/40 transition-colors"
              >
                <div>
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-1.5 min-w-0">
                      <MapPin className="size-3.5 text-primary shrink-0" />
                      <p className="text-sm font-semibold text-foreground truncate" title={place}>
                        {place}
                      </p>
                    </div>
                    <Badge variant={onlineCount > 0 ? 'success' : 'outline'} className="text-[10px] shrink-0">
                      {onlineCount > 0 ? `${onlineCount} Online` : 'Offline'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 truncate">
                    {screensAtPlace.map((s) => s.name).join(', ')}
                  </p>
                  {screensAtPlace[0]?.detail && (
                    <p className="text-[11px] text-muted-foreground font-mono mt-0.5">
                      {screensAtPlace[0].detail}
                    </p>
                  )}
                </div>

                <a
                  href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(place)}`}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-primary hover:underline inline-flex items-center gap-1 pt-1.5 border-t border-border/40"
                >
                  Search on Google Maps &rarr;
                </a>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  if (points.length > 0) {
    return (
      <div className="border-hairline bg-muted/30 rounded-xl border border-dashed p-6 text-center">
        <MapPin className="text-muted-foreground/50 mx-auto size-6" aria-hidden="true" />
        <p className="text-foreground mt-2 text-sm font-medium">No venue locations specified</p>
        <p className="text-muted-foreground mt-1 text-sm">
          {points.length} screen{points.length === 1 ? ' is' : 's are'} assigned without a location. Open screen settings to set venue names or GPS pins.
        </p>
      </div>
    )
  }

  return (
    <div className="border-hairline bg-muted/30 rounded-xl border border-dashed p-6 text-center">
      <MapPin className="text-muted-foreground/50 mx-auto size-6" aria-hidden="true" />
      <p className="text-foreground mt-2 text-sm font-medium">Not scheduled on any screen yet</p>
      <p className="text-muted-foreground mt-1 text-sm">
        Add this ad to a playlist or booking to assign screens and show venue locations.
      </p>
    </div>
  )
}

/**
 * Where a set of screens physically are.
 *
 * Pins are tinted by whether the screen is reporting in, so a client-facing map doubles as
 * a health check: a grey pin is a screen that was sold but is not answering.
 */
export function ScreenMap({ points, height = 320 }: { points: MapPoint[]; height?: number }) {
  const located = useMemo(
    () => points.filter((p) => p.latitude != null && p.longitude != null),
    [points],
  )

  // 'disabled' when no key is configured, 'error' when Google rejects it (wrong referrer,
  // API not enabled, billing not linked). Either way there is still a map on the page.
  const sdk = useGoogleMaps('marker')

  if (!located.length) return <NoLocations points={points} />
  if (sdk === 'ready') return <GoogleMap points={located} height={height} />
  return <LeafletMap points={located} height={height} />
}
