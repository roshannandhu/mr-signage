import { create } from 'zustand'
import type { User } from './types'

/** The workspace a platform operator has stepped into, or null when they have not. */
export interface ActingWorkspace {
  id: number
  name: string
  // "View workspace (read-only)" from the admin Tenant 360 page. Every write the frontend
  // sends while this is set carries X-Act-As-Mode: read, and the server enforces it --
  // this only changes the UI's own behaviour (the banner, and what it warns about).
  readOnly?: boolean
}

interface AuthState {
  token: string | null
  user: User | null
  /** Super-admin only. Every API call carries it as X-Act-As-Org while it is set. */
  acting: ActingWorkspace | null
  hydrated: boolean
  hydrate: () => void
  setSession: (token: string, user: User) => void
  setUser: (user: User | null) => void
  enterWorkspace: (workspace: ActingWorkspace) => void
  leaveWorkspace: () => void
  clearSession: () => void
}

// Persisted, so a reload inside a workspace does not silently drop the operator back to
// platform-wide scope while the page still says they are inside one.
const ACTING_KEY = 'acting_workspace'

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  acting: null,
  hydrated: false,
  hydrate: () => {
    const token = localStorage.getItem('token')
    const rawUser = localStorage.getItem('user')
    let user: User | null = null
    if (rawUser) {
      try {
        user = JSON.parse(rawUser) as User
      } catch {
        localStorage.removeItem('user')
      }
    }
    let acting: ActingWorkspace | null = null
    const rawActing = localStorage.getItem(ACTING_KEY)
    if (rawActing) {
      try {
        acting = JSON.parse(rawActing) as ActingWorkspace
      } catch {
        localStorage.removeItem(ACTING_KEY)
      }
    }
    set({ token, user, acting, hydrated: true })
  },
  setSession: (token, user) => {
    localStorage.setItem('token', token)
    localStorage.setItem('user', JSON.stringify(user))
    // A fresh sign-in is never inside somebody else's workspace.
    localStorage.removeItem(ACTING_KEY)
    set({ token, user, acting: null })
  },
  setUser: (user) => {
    if (user) localStorage.setItem('user', JSON.stringify(user))
    else localStorage.removeItem('user')
    set({ user })
  },
  enterWorkspace: (workspace) => {
    localStorage.setItem(ACTING_KEY, JSON.stringify(workspace))
    set({ acting: workspace })
  },
  leaveWorkspace: () => {
    localStorage.removeItem(ACTING_KEY)
    set({ acting: null })
  },
  clearSession: () => {
    localStorage.removeItem('token')
    localStorage.removeItem('user')
    localStorage.removeItem(ACTING_KEY)
    set({ token: null, user: null, acting: null })
  },
}))
