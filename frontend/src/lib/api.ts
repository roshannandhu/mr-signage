import { useAuthStore } from './store'
import type {
  PlatformStorage, Package, PackageWrite, FleetOverview, ScreenUpdateResult, TenantSummary, TenantScreen, TenantContent, TenantUser, AlertSummary, Branding, Client, TenantPlan, FleetAlert, Placement, PaymentMethod, PlanOption, MediaReport, FitMode, OperatingMode, RolloutState, SyncRole, AppRelease, BillingSummary, Campaign, CampaignExportFormat, CampaignInfo, CampaignPoint, CampaignStats, CheckoutSession, PurchaseResponse, CustomPlanRequestInput, CustomPlanRequestItem, CustomPlanRequestUpdate, ContentItem, PlatformPayment, EmergencyBroadcast, EnrollmentToken, ItemSchedule, Plan, Playlist, PlaylistItem, Screen, TenantRole, ScreenGroup, Screenshot, TransitionName, User } from './types'

const PROD_API_URL = 'https://olrac-signage-32lh.onrender.com'
const configuredUrl = (process.env.NEXT_PUBLIC_API_URL || PROD_API_URL).replace(/\/$/, '')
let API_BASE = `${configuredUrl}/api`
export let API_HOST = configuredUrl

if (typeof window !== 'undefined') {
  try {
    const isLocalhost = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1'
    if (!isLocalhost && (API_BASE.includes('localhost') || API_BASE.includes('127.0.0.1'))) {
      API_BASE = `${PROD_API_URL}/api`
      API_HOST = PROD_API_URL
    } else if (isLocalhost && !process.env.NEXT_PUBLIC_API_URL) {
      API_BASE = 'http://localhost:8000/api'
      API_HOST = 'http://localhost:8000'
    }
  } catch {
    // Keep configured value
  }
}

export const WS_BASE = API_BASE.replace(/^http/, 'ws')

/**
 * Absolute, fetchable URL for a stored media location.
 *
 * Pure string work: no credentials, no clock, no signing, nothing that expires. The API has
 * already done the resolving — `ContentResponse.absolutise_urls` runs `resolve_media_url`
 * over both `file_url` and `thumbnail`, so what arrives here is already
 * `https://<api>/api/media/<key>`, a URL that signs a fresh redirect on every request and
 * stays valid for as long as the object exists.
 *
 * This used to take that correct URL and mint a 7-day presigned R2 URL from credentials
 * bundled into the page, and THAT is why "thumbnail not showing" kept coming back rather
 * than being fixed once. A second signer has to agree with the backend about bucket,
 * endpoint, region, key prefix and clock; any drift, any rotated key, any URL still cached
 * after its window closes, and the result is an opaque 403 that renders as a grey box
 * indistinguishable from a missing file. The backend retired its own presigner for exactly
 * this reason (see `resolve_media_url` in backend/media_urls.py) and the TV app deliberately
 * signs nothing (see `rewriteLoopbackMediaUrl` in ApiClient.kt). The dashboard was the last
 * holdout. It also meant every visitor was served the storage credentials.
 *
 * If a thumbnail is blank now, the answer is in the response or the server — not here.
 */
export function resolveMediaUrl(urlStr: string | null | undefined): string | undefined {
  if (!urlStr) return undefined
  // Already absolute, which is the ordinary case: hand it back untouched.
  if (/^https?:\/\//i.test(urlStr)) return urlStr
  // A bare stored path, from a response that predates absolutising or was cached before it.
  if (urlStr.startsWith('/uploads/')) return `${API_HOST}${urlStr}`
  if (urlStr.startsWith('uploads/')) return `${API_HOST}/${urlStr}`
  if (urlStr.startsWith('/api/media/')) return `${API_HOST}${urlStr}`
  // s3:// is a storage location, not a URL. The API resolves these; if one reaches the
  // browser the row was serialised by something that skipped absolutise_urls, and guessing
  // at a signed URL here is what this function is no longer in the business of doing.
  return urlStr
}


export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

/**
 * Save a blob to the user's downloads.
 *
 * One implementation because the object URL has to be revoked afterwards, and a copy of
 * this that forgets to leaks the whole file for the life of the tab.
 */
function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function authFetch(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const { token, acting } = useAuthStore.getState()
  const headers = new Headers(options.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  // Attached here rather than at each call site so stepping into a workspace reaches every
  // existing tenant endpoint at once -- which is the whole reason admin does not need its
  // own parallel copies of them. The server ignores it for anyone but a super admin.
  if (acting) headers.set('X-Act-As-Org', String(acting.id))

  const response = await fetch(`${API_BASE}${endpoint}`, { ...options, headers })
  if (response.status === 401) {
    useAuthStore.getState().clearSession()
    if (typeof window !== 'undefined') window.location.assign('/login')
    throw new ApiError('Your session has expired', 401)
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = typeof payload?.detail === 'string' ? payload.detail : 'Something went wrong'
    throw new ApiError(detail, response.status)
  }
  return response
}

async function fetchWithAuth<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const response = await authFetch(endpoint, options)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const api = {
  login: async (username: string, password: string) => {
    const response = await fetch(`${API_BASE}/auth/token`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({ username, password }),
    })
    if (!response.ok) throw new ApiError('The username or password is incorrect', response.status)
    return response.json() as Promise<{ access_token: string; token_type: string; user: User }>
  },
  authMethods: async () => {
    try {
      const response = await fetch(`${API_BASE}/auth/methods`)
      if (!response.ok) return { google: true, password: true }
      return (await response.json()) as { google: boolean; password: boolean }
    } catch {
      return { google: true, password: true }
    }
  },
  googleAuthUrl: async (redirectUri: string) => {
    const response = await fetch(`${API_BASE}/auth/google/url?redirect_uri=${encodeURIComponent(redirectUri)}`)
    if (!response.ok) return { url: null }
    return response.json() as Promise<{ url: string | null }>
  },

  loginWithGoogle: async (code: string, redirectUri: string) => {
    const response = await fetch(`${API_BASE}/auth/google`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code, redirect_uri: redirectUri }),
    })
    if (!response.ok) {
      const detail = await response.json().catch(() => null)
      throw new ApiError(detail?.detail || 'Google sign-in failed', response.status)
    }
    return response.json() as Promise<{ access_token: string; token_type: string; user: User }>
  },
  /**
   * Whether the server can actually serve what the dashboard is about to render.
   *
   * Unauthenticated, like the endpoint. Used to say WHY a thumbnail is blank: without
   * object storage every /api/media/* request is a 503 and every tile renders as a grey
   * box indistinguishable from a missing file, which has cost days of hunting the wrong
   * thing more than once.
   */
  health: () =>
    fetch(`${API_BASE}/health`).then((r) => r.json() as Promise<{
      status: string
      object_storage?: string
      redis?: string
      warning?: string | null
    }>),
  me: () => fetchWithAuth<User>('/auth/me'),
  updateProfile: (data: { full_name?: string | null; email?: string | null }) =>
    fetchWithAuth<User>('/auth/me', {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  changePassword: (data: { current_password: string; new_password: string }) =>
    fetchWithAuth<void>('/auth/change-password', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  getScreens: () => fetchWithAuth<Screen[]>('/screens/'),
  resolveLocationLink: (link: string) =>
    fetchWithAuth<{ latitude: number; longitude: number; name: string | null }>(
      '/screens/resolve-location-link',
      // Without this header fetch() labels a string body text/plain, FastAPI rejects it
      // before the handler ever runs, and the validation error it returns carries a list
      // in `detail` where authFetch expects a string -- so every link, however valid,
      // failed as "Something went wrong". The other 25 JSON calls here already set it.
      { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ link }) },
    ),
  // Partial by design: sending only the edited keys stops a rename from resetting
  // orientation, which is not editable anywhere in the dashboard.
  patchScreen: (id: number, data: Partial<{
    name: string
    orientation: number
    group_id: number | null
    target_version_code: number | null
    description: string | null
    tags: string | null
    location: string | null
    latitude: number | null
    longitude: number | null
    place_id: string | null
    timezone: string | null
    fit_mode: FitMode
    /** Four digits. The API rejects any other shape. */
    maintenance_pin: string
    sync_playback: boolean
    sync_role: SyncRole
    leader_screen_id: number | null
    operating_mode: OperatingMode
    operating_hours: Record<string, [string, string]> | null
  }>) => fetchWithAuth<Screen>(`/screens/${id}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  pairScreen: (pairCode: string) => fetchWithAuth<Screen>('/screens/pair', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pair_code: pairCode }),
  }),
  revokeScreenDeviceSecret: (screenId: number) => fetchWithAuth(`/screens/${screenId}/device-secret`, { method: 'DELETE' }),
  // 204, so there is no body to read -- typed void rather than an object the caller would
  // find undefined at runtime. Archives the screen: play history keeps its attribution and
  // the panel is signed out of the workspace on its next contact.
  deleteScreen: (screenId: number) => fetchWithAuth<void>(`/screens/${screenId}`, { method: 'DELETE' }),
  assignPlaylist: (screenId: number, playlistId: number) =>
    fetchWithAuth(`/screens/${screenId}/assign/${playlistId}`, { method: 'POST' }),
  clearScreenAssignment: (screenId: number) =>
    fetchWithAuth(`/screens/${screenId}/assign`, { method: 'DELETE' }),
  ensureScreenPlaylist: (screenId: number) =>
    fetchWithAuth<Screen>(`/screens/${screenId}/ensure-playlist`, { method: 'POST' }),

  getGroups: () => fetchWithAuth<ScreenGroup[]>('/groups/'),
  createGroup: (name: string) => fetchWithAuth<ScreenGroup>('/groups/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  }),
  setGroupScreens: (groupId: number, screenIds: number[]) => fetchWithAuth<ScreenGroup>(`/groups/${groupId}/screens`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ screen_ids: screenIds }),
  }),
  assignGroupPlaylist: (groupId: number, playlistId: number) =>
    fetchWithAuth<ScreenGroup>(`/groups/${groupId}/assign/${playlistId}`, { method: 'POST' }),
  renameGroup: (groupId: number, name: string) => fetchWithAuth<ScreenGroup>(`/groups/${groupId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  }),
  deleteGroup: (groupId: number) => fetchWithAuth<{ status: string }>(`/groups/${groupId}`, { method: 'DELETE' }),

  getContent: () => fetchWithAuth<ContentItem[]>('/content/'),

  getContentItem: (contentId: number) => fetchWithAuth<ContentItem>(`/content/${contentId}`),
  getMediaReport: (contentId: number) => fetchWithAuth<MediaReport>(`/analytics/media/${contentId}`),
  // Uses XHR rather than fetch purely for upload progress: fetch cannot report it, and a
  // several-hundred-megabyte advert otherwise sits on "uploading" for minutes with no sign
  // of life. Error and 401 handling mirror authFetch.
  uploadContent: (file: File, name: string, tags: string, onProgress?: (percent: number) => void) =>
    new Promise<ContentItem>((resolve, reject) => {
      const formData = new FormData()
      formData.append('file', file)
      formData.append('name', name)
      if (tags.trim()) formData.append('tags', tags)

      const request = new XMLHttpRequest()
      request.open('POST', `${API_BASE}/content/upload`)
      const { token, acting } = useAuthStore.getState()
      if (token) request.setRequestHeader('Authorization', `Bearer ${token}`)
      // The same header authFetch sets. Without it, an upload made while a platform operator is
      // inside a tenant's workspace was filed under the operator's own organisation.
      if (acting) request.setRequestHeader('X-Act-As-Org', String(acting.id))

      request.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress?.(Math.round((event.loaded / event.total) * 100))
      }
      request.onload = () => {
        if (request.status === 401) {
          useAuthStore.getState().clearSession()
          if (typeof window !== 'undefined') window.location.assign('/login')
          reject(new ApiError('Your session has expired', 401))
          return
        }
        if (request.status >= 200 && request.status < 300) {
          try {
            resolve(JSON.parse(request.responseText) as ContentItem)
          } catch {
            reject(new ApiError('The server returned an unreadable response', request.status))
          }
          return
        }
        let detail = 'Something went wrong'
        try {
          const payload = JSON.parse(request.responseText)
          if (typeof payload?.detail === 'string') detail = payload.detail
        } catch {
          // keep the generic message
        }
        reject(new ApiError(detail, request.status))
      }
      request.onerror = () => reject(new ApiError('The upload could not reach the server', 0))
      request.send(formData)
    }),
  deleteContent: (id: number) => fetchWithAuth(`/content/${id}`, { method: 'DELETE' }),
  retryContentProcessing: (id: number) => fetchWithAuth<ContentItem>(`/content/${id}/retry`, { method: 'POST' }),
  updateContentClientAd: (
    contentId: number,
    body: {
      name?: string
      client_name: string
      client_email?: string
      client_phone?: string
      plan_id?: number | null
      // Omitted leaves the agreed price alone; sent, it IS the agreed price. A booking on
      // no plan has no other source for one.
      price_paise?: number
      // The booking's own run length. Omitted on a plan sale -- the package states it.
      duration_days?: number
      screen_ids?: number[]
      // Keyed by screen id. {} clears a bespoke booking back to one uniform window;
      // omitting it leaves the existing lengths alone.
      screen_days?: Record<number, number>
    clear_screen_days?: boolean
      notes?: string
    }
  ) =>
    fetchWithAuth<ContentItem>(`/content/${contentId}/client-ad`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),

  generateProvisioningQr: (data: {
    wifi_ssid: string
    wifi_password: string
    wifi_security_type: string
    max_uses: number
  }) => fetchWithAuth<Record<string, unknown>>('/provisioning/qr', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),

  // There used to be a `bookingReportPdfUrl` here, rendered straight into an <a href>.
  // That navigates without the Authorization header, so it 401'd every time -- the exact
  // trap the comment on downloadCampaignReport below already warned about. The report is
  // never stored anywhere: the endpoint builds the PDF per request and streams it, so the
  // only way to get at it is an authenticated fetch.
  // One fetcher for both documents. They are the same shape of request and differ only in
  // which PDF the server builds -- proof of delivery, or the bill.
  fetchBookingPdf: async (
    placementId: number,
    document: 'report' | 'invoice' = 'report',
  ): Promise<{ blob: Blob; filename: string }> => {
    const response = await authFetch(`/placements/${placementId}/${document}.pdf`)
    if (!response.ok) throw new ApiError(`The ${document} could not be generated.`, response.status)
    const disposition = response.headers.get('content-disposition') || ''
    const match = disposition.match(/filename="?([^"]+)"?/)
    return { blob: await response.blob(), filename: match?.[1] || `booking-${placementId}-${document}.pdf` }
  },

  fetchBookingReportPdf: (placementId: number) => api.fetchBookingPdf(placementId, 'report'),

  downloadBookingReport: async (placementId: number) => {
    const { blob, filename } = await api.fetchBookingPdf(placementId, 'report')
    saveBlob(blob, filename)
  },

  downloadInvoice: async (placementId: number) => {
    const { blob, filename } = await api.fetchBookingPdf(placementId, 'invoice')
    saveBlob(blob, filename)
  },

  /**
   * Hand the PDF to the device's share sheet, so a tenant can send it to their client from
   * WhatsApp, Gmail or anything else installed.
   *
   * Returns 'shared' | 'downloaded' so the caller can say which actually happened. Sharing
   * files is unsupported on desktop Firefox and parts of desktop Chrome, and Safari
   * additionally wants share() called inside the user gesture -- awaiting the fetch above
   * can cost that activation and throw NotAllowedError. Every one of those falls back to
   * saving the file rather than surfacing an error the operator can do nothing about.
   */
  shareBookingReport: async (placementId: number, title: string): Promise<'shared' | 'downloaded'> => {
    const { blob, filename } = await api.fetchBookingReportPdf(placementId)
    const file = new File([blob], filename, { type: 'application/pdf' })
    if (navigator.canShare?.({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title })
        return 'shared'
      } catch (error) {
        // The user dismissing the sheet is not a failure, and must not then dump a file
        // into their downloads folder as a consolation prize.
        if ((error as Error)?.name === 'AbortError') return 'shared'
      }
    }
    saveBlob(blob, filename)
    return 'downloaded'
  },
  getAllPlacements: () => fetchWithAuth<Placement[]>('/placements/'),
  getPlacements: (contentId: number) => fetchWithAuth<Placement[]>(`/placements/?content_id=${contentId}`),
  getEmailStatus: () => fetchWithAuth<{ is_configured: boolean; sender: string | null; missing_variables: string[] }>('/placements/email-status'),
  emailBookingReport: (placementId: number, document: 'report' | 'invoice' = 'report') =>
    fetchWithAuth<{ status: string; to: string }>(
      `/placements/${placementId}/email?document=${document}`, { method: 'POST' }),

  // --- Billing on a booking. `is_paid` is the shadow of these records, so these are the
  // only calls that change whether a booking counts as paid.
  //
  // ADDS a receipt; it does not replace the last one. Correcting a wrong amount is
  // deletePayment followed by recordPayment, which is also what leaves an honest ledger.
  recordPayment: (placementId: number, body: {
    amount_paise: number
    method: PaymentMethod
    reference?: string | null
    paid_at?: string | null
    notes?: string | null
  }) => fetchWithAuth<Placement>(`/placements/${placementId}/payments`, {
    // The header is not optional: without it fetch labels a string body text/plain and
    // FastAPI rejects it before the handler runs, returning a validation LIST in `detail`
    // where authFetch expects a string -- so a perfectly good payment fails as
    // "Something went wrong". resolveLocationLink above carries the same scar.
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),

  /** One receipt out -- a duplicate, or a cheque that bounced. The others still stand. */
  deletePayment: (placementId: number, paymentId: number) =>
    fetchWithAuth<Placement>(`/placements/${placementId}/payments/${paymentId}`,
      { method: 'DELETE' }),

  /** Correct a receipt in place: the money arrived, the record of it was wrong. Only the
   *  fields sent are touched. Money that never arrived is deletePayment instead. */
  updatePayment: (placementId: number, paymentId: number, body: {
    amount_paise?: number
    method?: PaymentMethod
    reference?: string | null
    paid_at?: string
  }) => fetchWithAuth<Placement>(`/placements/${placementId}/payments/${paymentId}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),

  /** Every receipt out: none of this was ever paid. Also the only way to clear a booking
   *  flagged paid before payments were recorded, which has no receipt to remove. */
  clearPayment: (placementId: number) =>
    fetchWithAuth<Placement>(`/placements/${placementId}/payments`, { method: 'DELETE' }),

  // --- Moving a client to a different plan.
  getPlanOptions: (placementId: number) =>
    fetchWithAuth<PlanOption[]>(`/placements/${placementId}/plan-options`),

  // REPLACES the booking's plan and price. Nothing is billed on top and no date moves --
  // selling more time is addPlacementExtension, which is a different sale.
  changePlan: (placementId: number, body: {
    // Null moves the booking OFF its package onto a negotiated price. Sending it is the
    // only way back to a custom sale; the endpoint used to refuse it.
    plan_id: number | null
    // The new TOTAL price, not a difference. Omitted takes the plan's list price.
    price_paise?: number | null
    // The custom screen cap, re-cut. Only in force off a package; 0 lifts it, omitted
    // leaves it alone. Refused if the booking already runs on more screens than that.
    max_locations?: number
  }) => fetchWithAuth<Placement>(`/placements/${placementId}/change-plan`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  /** Put a booking back on every screen it has fallen off. Only ever adds: a location
   *  that still holds its playlist item is left alone, so this cannot duplicate an
   *  advert into a loop. */
  replacePlacementTargets: (placementId: number) =>
    fetchWithAuth<Placement>(`/placements/${placementId}/replace`, { method: 'POST' }),

  createPlacement: (data: {
    content_id: number
    // One or the other: a client record is the supported path, a bare name still works.
    advertiser?: string
    client_id?: number | null
    // Naming a plan fills in price and, when ends_at is omitted, the end date from its
    // duration. Copied server side, so later repricing leaves this booking alone.
    plan_id?: number | null
    // How many screens a booking sold WITHOUT a package may cover. 0 (the default) is no
    // cap; a package states its own limit and overrides this.
    max_locations?: number
    price_paise: number
    is_paid: boolean
    starts_at: string
    ends_at?: string
    notes?: string | null
    targets: { screen_id?: number; group_id?: number; days?: number }[]
  }) => fetchWithAuth<Placement>('/placements/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),

  addPlacementExtension: (placementId: number, data: {
    extended_to: string; extended_from?: string
    additional_price_paise: number; is_paid: boolean; notes?: string | null
  }) => fetchWithAuth<Placement>(`/placements/${placementId}/extensions`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  removePlacementExtension: (placementId: number, extensionId: number) =>
    fetchWithAuth<Placement>(`/placements/${placementId}/extensions/${extensionId}`, { method: 'DELETE' }),

  getBranding: () => fetchWithAuth<Branding>('/branding/'),
  updateBranding: (data: { brand_name?: string | null; brand_color?: string | null }) =>
    fetchWithAuth<Branding>('/branding/', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  uploadBrandLogo: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    // No Content-Type header: the browser has to set the multipart boundary itself, and
    // naming it here produces a body FastAPI cannot parse.
    return fetchWithAuth<Branding>('/branding/logo', { method: 'POST', body: form })
  },
  removeBrandLogo: () => fetchWithAuth<Branding>('/branding/logo', { method: 'DELETE' }),

  getClients: () => fetchWithAuth<Client[]>('/clients/'),
  createClient: (data: { name: string; email?: string | null; phone?: string | null; notes?: string | null }) =>
    fetchWithAuth<Client>('/clients/', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  updateClient: (id: number, data: Partial<{ name: string; email: string | null; phone: string | null; notes: string | null }>) =>
    fetchWithAuth<Client>(`/clients/${id}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  deleteClient: (id: number) => fetchWithAuth<{ status: string }>(`/clients/${id}`, { method: 'DELETE' }),

  getTenantPlans: (includeInactive = false) =>
    fetchWithAuth<TenantPlan[]>(`/tenant-plans/?include_inactive=${includeInactive}`),
  createTenantPlan: (data: {
    name: string; description?: string | null; duration_days: number
    max_locations: number; ad_slots: number; price_paise: number; support_tier: string
  }) => fetchWithAuth<TenantPlan>('/tenant-plans/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  updateTenantPlan: (id: number, data: Partial<{
    name: string; description: string | null; duration_days: number
    max_locations: number; ad_slots: number; price_paise: number; support_tier: string; is_active: boolean
  }>) => fetchWithAuth<TenantPlan>(`/tenant-plans/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  deleteTenantPlan: (id: number) =>
    fetchWithAuth<{ status: string; bookings?: number }>(`/tenant-plans/${id}`, { method: 'DELETE' }),

  updatePlacement: (id: number, data: Partial<{
    advertiser: string; price_paise: number; is_paid: boolean
    client_id: number | null; plan_id: number | null
    starts_at: string; ends_at: string; notes: string | null
  }>) => fetchWithAuth<Placement>(`/placements/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  // `days` gives this location its own run length, the same "30 in the mall, 10 in the
  // shop" the create modal offers. The endpoint has always accepted it; not sending it made
  // the feature unreachable for any place added after the booking was made.
  addPlacementTarget: (id: number, target: { screen_id?: number; group_id?: number; days?: number }) =>
    fetchWithAuth<Placement>(`/placements/${id}/targets`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(target),
    }),
  removePlacementTarget: (id: number, targetId: number) =>
    fetchWithAuth<Placement>(`/placements/${id}/targets/${targetId}`, { method: 'DELETE' }),
  splitPlacementTarget: (id: number, targetId: number, excludeScreenIds: number[]) =>
    fetchWithAuth<Placement>(`/placements/${id}/targets/${targetId}/split`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ exclude_screen_ids: excludeScreenIds }),
    }),
  deletePlacement: (id: number) => fetchWithAuth(`/placements/${id}`, { method: 'DELETE' }),

  getEnrollmentTokens: () => fetchWithAuth<EnrollmentToken[]>('/enrollment-tokens/'),
  createEnrollmentToken: (data: { description?: string, expires_at?: string, max_uses?: number }) => 
    fetchWithAuth<EnrollmentToken>('/enrollment-tokens/', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data)
    }),
  revokeEnrollmentToken: (id: number) => fetchWithAuth(`/enrollment-tokens/${id}`, { method: 'DELETE' }),

  getPlaylists: () => fetchWithAuth<Playlist[]>('/playlists/'),
  createPlaylist: (name: string) => fetchWithAuth<Playlist>('/playlists/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }),
  }),
  getPlaylist: (id: number) => fetchWithAuth<Playlist>(`/playlists/${id}`),
  reorderPlaylistItems: (id: number, orders: number[]) => fetchWithAuth(`/playlists/${id}/items/reorder`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(orders),
  }),
  // duration is deliberately omitted: the server defaults a video to its real length
  // (from ffprobe) and only falls back to 10s for images. Sending 10 here would
  // truncate every advert longer than ten seconds.
  addPlaylistItem: (id: number, contentId: number, order: number) => fetchWithAuth(`/playlists/${id}/items`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content_id: contentId, order }),
  }),
  updatePlaylistItem: (playlistId: number, itemId: number, data: Partial<{
    duration: number
    rotation: number | null
    start_at: string | null
    end_at: string | null
    schedule: Omit<ItemSchedule, 'id'> | null
    transition: TransitionName | null
    transition_ms: number | null
  }>) => fetchWithAuth<PlaylistItem>(`/playlists/${playlistId}/items/${itemId}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  updatePlaylistTransitions: (playlistId: number, data: { transition: TransitionName; transition_ms: number; apply_to_all: boolean }) =>
    fetchWithAuth<Playlist>(`/playlists/${playlistId}/transitions`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
    }),
  removePlaylistItem: (playlistId: number, itemId: number) =>
    fetchWithAuth(`/playlists/${playlistId}/items/${itemId}`, { method: 'DELETE' }),

  getUsers: () => fetchWithAuth<User[]>('/users/'),
  // TenantRole, not Role: the API refuses to create a platform account here, so the
  // type should not let the dashboard try.
  createUser: (data: { username: string; password: string; role: TenantRole }) => fetchWithAuth<User>('/users/', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),
  updateUser: (id: number, data: { role?: TenantRole; is_active?: boolean }) => fetchWithAuth<User>(`/users/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  }),

  getBillingSummary: () => fetchWithAuth<BillingSummary>('/billing/summary'),
  getPlans: () => fetchWithAuth<Plan[]>('/billing/plans'),
  createCheckout: (planId: number, billingPeriod: 'monthly' | 'yearly') => fetchWithAuth<CheckoutSession>('/billing/checkout', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_id: planId, billing_period: billingPeriod }),
  }),
  // Storefront: buy a package as a one-time charge for its access period.
  purchasePlan: (planId: number) => fetchWithAuth<PurchaseResponse>('/billing/purchase', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_id: planId }),
  }),
  // Finish a mock (test) purchase; refused by the server under a real provider.
  mockConfirmOrder: (orderId: string) => fetchWithAuth<PurchaseResponse>('/billing/mock/confirm', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ order_id: orderId }),
  }),
  // Custom plans: submit a bespoke shape, list your own requests, pay a priced one.
  submitCustomRequest: (body: CustomPlanRequestInput) => fetchWithAuth<CustomPlanRequestItem>('/billing/custom-request', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
  getMyCustomRequests: () => fetchWithAuth<CustomPlanRequestItem[]>('/billing/custom-request'),
  purchaseCustomRequest: (id: number) => fetchWithAuth<PurchaseResponse>(`/billing/custom-request/${id}/purchase`, { method: 'POST' }),

  // P6/P7 Endpoints
  getEmergencyBroadcasts: () => fetchWithAuth<EmergencyBroadcast[]>('/emergency/active'),
  triggerEmergencyBroadcast: (data: { target_type: string, target_id: number | null, playlist_id: number }) =>
    fetchWithAuth('/emergency/broadcast', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  // POST /emergency/cancel is the route the backend has; this used to send DELETE
  // /emergency/broadcast, which does not exist, so every cancel answered 405 and the override
  // stayed on the screens.
  cancelEmergencyBroadcast: (data: { target_type: string, target_id: number | null, playlist_id: number }) =>
    fetchWithAuth('/emergency/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
    
  getScreenshots: (screenId: number) => fetchWithAuth<Screenshot[]>(`/screenshots/${screenId}/screenshots`),
  requestScreenshot: (screenId: number) => fetchWithAuth(`/screenshots/${screenId}/request-screenshot`, { method: 'POST' }),
  bringToFront: (screenId: number) => fetchWithAuth(`/screens/${screenId}/bring-to-front`, { method: 'POST' }),

  getCampaigns: () => fetchWithAuth<Campaign[]>('/analytics/campaigns'),
  getCampaign: (id: number) => fetchWithAuth<CampaignInfo>(`/analytics/campaigns/${id}`),
  getCampaignStats: (id: number) => fetchWithAuth<CampaignStats>(`/analytics/campaigns/${id}/stats`),
  getCampaignTimeseries: (id: number) => fetchWithAuth<CampaignPoint[]>(`/analytics/campaigns/${id}/timeseries`),
  // The export endpoint authenticates by header like every other route, so it cannot
  // be reached by navigating to a ?token= URL: that 401s and leaks the JWT into history.
  downloadCampaignReport: async (id: number, format: CampaignExportFormat) => {
    const response = await authFetch(`/analytics/campaigns/${id}/export?format=${format}`)
    const blob = await response.blob()
    saveBlob(blob, `campaign_${id}_report.${format === 'excel' ? 'xlsx' : format}`)
  },

  getAlerts: (includeResolved = false) =>
    fetchWithAuth<FleetAlert[]>(`/alerts/?include_resolved=${includeResolved}`),
  getAlertSummary: () => fetchWithAuth<AlertSummary>('/alerts/summary'),
  acknowledgeAlert: (id: number) =>
    fetchWithAuth<FleetAlert>(`/alerts/${id}/acknowledge`, { method: 'POST' }),

  getReleases: () => fetchWithAuth<AppRelease[]>('/releases/'),
  // sha256 is required, not nullable: the player refuses to install an APK it cannot
  // verify, so a release without one could never reach a screen.
  createRelease: (data: { version_code: number, version_name: string, apk_url: string, sha256: string, mandatory: boolean }) =>
    fetchWithAuth<AppRelease>('/releases/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) }),
  promoteRelease: (versionCode: number, rolloutState: RolloutState) =>
    fetchWithAuth<AppRelease>(`/releases/${versionCode}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rollout_state: rolloutState }) }),
}

/**
 * Platform-operator calls. Separate object from `api` so it is obvious at the call site
 * that these are cross-tenant, and so a tenant page cannot reach for one by accident.
 *
 * Everything here goes through the same authFetch as the rest of the app, which matters
 * for two reasons the previous admin pages got wrong: they used bare fetch() with a
 * hand-built Authorization header, so an expired token showed an empty table instead of
 * redirecting to login; and they used RELATIVE `/api/...` URLs, which only resolve through
 * the dev-only rewrite in next.config.ts -- in production those requests hit the Vercel
 * origin and 404, so the whole approvals screen was dead once deployed.
 */
export const adminApi = {
  listTenants: (status?: string) =>
    fetchWithAuth<TenantSummary[]>(`/admin/tenants${status ? `?status=${encodeURIComponent(status)}` : ''}`),
  getTenant: (id: number) => fetchWithAuth<TenantSummary>(`/admin/tenants/${id}`),
  getTenantScreens: (id: number) => fetchWithAuth<TenantScreen[]>(`/admin/tenants/${id}/screens`),
  getTenantContent: (id: number) => fetchWithAuth<TenantContent[]>(`/admin/tenants/${id}/content`),
  getTenantUsers: (id: number) => fetchWithAuth<TenantUser[]>(`/admin/tenants/${id}/users`),

  approveTenant: (id: number, body: { plan_id?: number; max_screens?: number; max_ad_slots?: number }) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/approve`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  rejectTenant: (id: number, reason: string) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/reject`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ reason }),
    }),
  suspendTenant: (id: number) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/suspend`, { method: 'POST' }),
  reinstateTenant: (id: number) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/reinstate`, { method: 'POST' }),
  updateQuota: (id: number, body: { plan_id?: number; max_screens?: number; max_ad_slots?: number }) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/quota`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  // The paid window. Separate from suspend/reinstate on purpose: suspension is a judgement
  // about the customer, expiry is a fact about the calendar.
  updateSubscription: (
    id: number,
    body: { extend_days?: number; period_end?: string; billing_period?: string; status?: 'active' | 'expired' },
  ) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/subscription`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  // Limits and features for one workspace, without touching the package everyone else is on.
  grantLimits: (
    id: number,
    body: {
      max_screens?: number; max_ad_slots?: number; max_clients?: number
      max_storage_bytes?: number; features?: Record<string, boolean>; days?: number; name?: string
    },
  ) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/grant`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),

  // Take a workspace off the platform. Nothing is destroyed today: the server marks it and
  // a nightly job purges it 30 days later, which is the window `restoreTenant` undoes it in.
  removeTenant: (id: number) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}`, { method: 'DELETE' }),
  restoreTenant: (id: number) =>
    fetchWithAuth<TenantSummary>(`/admin/tenants/${id}/restore`, { method: 'POST' }),
  // Skip the rest of the 30 days: rows and files go now. Only for a removed workspace, and the
  // server checks the typed name as well.
  deleteTenantPermanently: (id: number, confirmName: string) =>
    fetchWithAuth<{ name: string; rows: Record<string, number> }>(
      `/admin/tenants/${id}/permanent?confirm_name=${encodeURIComponent(confirmName)}`,
      { method: 'DELETE' },
    ),

  // What the bucket actually holds, and which workspace put it there. `refresh` forces a
  // re-count; the server caches it because LIST is billed per request.
  getStorage: (refresh = false) =>
    fetchWithAuth<PlatformStorage>(`/admin/storage${refresh ? '?refresh=true' : ''}`),
  // One PIN that opens any screen which has not been given its own. A kiosked panel with no
  // PIN has no exit at all — home and back are swallowed — so this is the fallback that
  // stops a factory reset being the only way back in.
  getMaintenancePin: () => fetchWithAuth<{ pin: string | null }>('/admin/maintenance-pin'),
  setMaintenancePin: (pin: string) =>
    fetchWithAuth<{ pin: string | null }>('/admin/maintenance-pin', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
    }),
  listPackages: () => fetchWithAuth<Package[]>('/admin/plans'),
  createPackage: (body: PackageWrite) =>
    fetchWithAuth<Package>('/admin/plans', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  updatePackage: (id: number, body: Partial<PackageWrite>) =>
    fetchWithAuth<Package>(`/admin/plans/${id}`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    }),
  deletePackage: (id: number) =>
    fetchWithAuth<{ status: string; detail?: string }>(`/admin/plans/${id}`, { method: 'DELETE' }),

  // Custom-plan request queue.
  getFleet: () => fetchWithAuth<FleetOverview>('/admin/fleet'),
  // Make one TV look for its update now. Omit versionCode for the latest released build
  // (which also clears any pin); pass one to pin the screen to that build.
  updateScreenNow: (screenId: number, versionCode?: number | null) =>
    fetchWithAuth<ScreenUpdateResult>(`/releases/screens/${screenId}/update`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(versionCode ? { version_code: versionCode } : {}),
    }),
  listCustomRequests: () => fetchWithAuth<CustomPlanRequestItem[]>('/admin/custom-requests'),
  priceCustomRequest: (id: number, pricePaise: number) =>
    fetchWithAuth<CustomPlanRequestItem>(`/admin/custom-requests/${id}/price`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ price_paise: pricePaise }),
    }),
  updateCustomRequest: (id: number, body: CustomPlanRequestUpdate) =>
    fetchWithAuth<CustomPlanRequestItem>(`/admin/custom-requests/${id}`, {
      method: 'PATCH',
      // Without this the body went as text/plain, FastAPI would not parse it as JSON, and every
      // save answered 422 -- the only JSON call in this file that forgot the header.
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  rejectCustomRequest: (id: number) =>
    fetchWithAuth<CustomPlanRequestItem>(`/admin/custom-requests/${id}/reject`, { method: 'POST' }),

  // Billing: what tenants have paid the platform (view only), the contact address a custom
  // request's mailto draft is addressed to, and whether SMTP actually works.
  listPayments: (filters?: { organization_id?: number; since?: string; until?: string }) => {
    const params = new URLSearchParams()
    if (filters?.organization_id) params.set('organization_id', String(filters.organization_id))
    if (filters?.since) params.set('since', filters.since)
    if (filters?.until) params.set('until', filters.until)
    const qs = params.toString()
    return fetchWithAuth<PlatformPayment[]>(`/admin/payments${qs ? `?${qs}` : ''}`)
  },
  getContactEmail: () => fetchWithAuth<{ email: string | null }>('/admin/settings/contact-email'),
  setContactEmail: (email: string) =>
    fetchWithAuth<{ email: string | null }>('/admin/settings/contact-email', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email }),
    }),
  getPlatformEmailStatus: () =>
    fetchWithAuth<{ is_configured: boolean; sender: string | null; missing_variables: string[] }>('/admin/settings/email-status'),
  sendTestEmail: (to: string) =>
    fetchWithAuth<{ status: string; to: string }>('/admin/settings/test-email', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ to }),
    }),

  getDemoVideo: () => fetchWithAuth<{ url: string; description?: string }>('/admin/demo-video'),
  setDemoVideo: (url: string, description?: string) =>
    fetchWithAuth<{ status: string; url: string }>('/admin/demo-video', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ url, description }),
    }),
  uploadDemoVideo: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return fetchWithAuth<{ status: string; url: string; message: string }>('/admin/demo-video/upload', {
      method: 'POST', body: form,
    })
  },

  updateUserRole: (userId: number, role: 'super_admin' | 'owner' | 'editor' | 'viewer') =>
    fetchWithAuth<{ status: string; user_id: number; username: string; role: string; message: string }>(`/admin/users/${userId}/role`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role }),
    }),
}

