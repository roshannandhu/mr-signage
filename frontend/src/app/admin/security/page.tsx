'use client'

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Check,
  CheckCircle,
  Copy,
  Crown,
  Eye,
  EyeOff,
  Globe,
  Key,
  KeyRound,
  LogOut,
  Plus,
  RefreshCw,
  Shield,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Tv,
  Unlock,
  UserPlus,
} from 'lucide-react'
import { adminApi } from '@/lib/api'
import { PageHeader, StatCard } from '@/components/admin/admin-ui'
import { useConfirm } from '@/components/admin/kit/confirm-dialog'
import { toast } from 'sonner'

export default function AdminSecurityPage() {
  const queryClient = useQueryClient()
  const confirm = useConfirm()
  const [activeTab, setActiveTab] = useState<'admins' | 'sign_ins' | 'kiosk_unlocks' | 'act_as'>('admins')

  // Maintenance PIN reveal & rotate state
  const [revealedPin, setRevealedPin] = useState<string | null>(null)
  const [revealModalOpen, setRevealModalOpen] = useState(false)
  const [revealReason, setRevealReason] = useState('')
  const [rotatePinModalOpen, setRotatePinModalOpen] = useState(false)
  const [newPinValue, setNewPinValue] = useState('')

  const setPinMutation = useMutation({
    mutationFn: (newPin: string) => adminApi.setMaintenancePin(newPin),
    onSuccess: (res) => {
      toast.success(
        res.pin
          ? `Master fleet maintenance PIN set to ${res.pin}`
          : 'Master fleet maintenance PIN cleared',
      )
      setRotatePinModalOpen(false)
      setNewPinValue('')
      setRevealedPin(null)
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'maintenance-pin'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Query security overview
  const { data, isFetching } = useQuery({
    queryKey: ['admin', 'security', 'overview'],
    queryFn: adminApi.getSecurityOverview,
    refetchInterval: 30000,
  })

  // Sign out everywhere mutation
  const signOutMutation = useMutation({
    mutationFn: (userId: number) => adminApi.signOutEverywhere(userId),
    onSuccess: (res) => {
      toast.success(`User sessions invalidated (token version: ${res.token_version})`)
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Add / Remove Admin state & mutations
  const [addAdminModalOpen, setAddAdminModalOpen] = useState(false)
  const [newAdminEmail, setNewAdminEmail] = useState('')
  const [newAdminFullName, setNewAdminFullName] = useState('')
  const [newAdminAuthMethod, setNewAdminAuthMethod] = useState<'google' | 'password_and_google'>('google')
  const [newAdminPassword, setNewAdminPassword] = useState('')

  const createAdminMutation = useMutation({
    mutationFn: () =>
      adminApi.createAdmin({
        email: newAdminEmail.trim(),
        full_name: newAdminFullName.trim() || undefined,
        auth_method: newAdminAuthMethod,
        password: newAdminAuthMethod === 'password_and_google' ? newAdminPassword : undefined,
      }),
    onSuccess: (res) => {
      toast.success(`Super Administrator ${res.email} provisioned successfully`)
      setAddAdminModalOpen(false)
      setNewAdminEmail('')
      setNewAdminFullName('')
      setNewAdminPassword('')
      setNewAdminAuthMethod('google')
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const removeAdminMutation = useMutation({
    mutationFn: (adminId: number) => adminApi.removeAdmin(adminId),
    onSuccess: () => {
      toast.success('Super Administrator removed and sessions invalidated')
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Policy update mutation
  const policyMutation = useMutation({
    mutationFn: (requireGoogle: boolean) =>
      adminApi.updateSecurityPolicy({ require_super_admin_google_sso: requireGoogle }),
    onSuccess: (res) => {
      toast.success(
        res.require_super_admin_google_sso
          ? 'Enforced: Super admins must now authenticate via Google SSO'
          : 'Policy relaxed: Password authentication permitted for super admins',
      )
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Reveal PIN mutation (Level-2 confirmation)
  const revealMutation = useMutation({
    mutationFn: (reason: string) => adminApi.revealMaintenancePin(reason),
    onSuccess: (res) => {
      setRevealedPin(res.pin)
      setRevealModalOpen(false)
      setRevealReason('')
      toast.success('Master maintenance PIN unmasked (audited)')
    },
    onError: (err: Error) => toast.error(err.message),
  })

  // Challenge-response state
  const [challengeModalOpen, setChallengeModalOpen] = useState(false)
  const [challengeDeviceId, setChallengeDeviceId] = useState('')
  const [challengeNonce, setChallengeNonce] = useState('')
  const [challengePassword, setChallengePassword] = useState('')
  const [challengeReason, setChallengeReason] = useState('')
  const [copiedChallenge, setCopiedChallenge] = useState(false)
  const [challengeResult, setChallengeResult] = useState<{
    device_id: string
    nonce: string
    challenge_code: string
    response_code?: string
    expires_at: string
    validity_window_seconds: number
  } | null>(null)

  const challengeMutation = useMutation({
    mutationFn: () =>
      adminApi.generateChallengeResponse({
        device_id: challengeDeviceId.trim(),
        nonce: challengeNonce.trim(),
        admin_password: challengePassword,
        reason: challengeReason.trim(),
      }),
    onSuccess: (res) => {
      setChallengeResult(res)
      setChallengePassword('')
      toast.success('Dynamic challenge-response code generated (audited)')
      queryClient.invalidateQueries({ queryKey: ['admin', 'security', 'overview'] })
    },
    onError: (err: Error) => toast.error(err.message),
  })

  const superAdmins = data?.super_admins ?? []
  const recentSignIns = data?.recent_sign_ins ?? []
  const recentUnlocks = data?.recent_kiosk_unlocks ?? []
  const actAsEvents = data?.act_as_events ?? []

  return (
    <div className="space-y-6 p-3.5 sm:p-6 lg:p-8 text-foreground">
      <PageHeader
        title="Security & Trust Operations Center"
        description="Operator privilege administration, session revocation, sign-in monitoring, and kiosk lockdown governance"
      >
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['admin', 'security'] })}
          className="flex items-center gap-2 rounded-xl border border-border bg-muted px-3.5 py-1.5 text-xs sm:text-sm text-muted-foreground hover:bg-muted/80"
        >
          <RefreshCw className={`size-3.5 ${isFetching ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </PageHeader>

      {/* Top metrics grid */}
      <div className="grid grid-cols-2 gap-2.5 sm:gap-4 lg:grid-cols-4">
        <StatCard label="Super Administrators" value={superAdmins.length} icon={ShieldCheck} accent="violet" />
        <StatCard
          label="Google SSO Enforced"
          value={data?.require_super_admin_google_sso ? 'Active' : 'Disabled'}
          icon={Globe}
          accent={data?.require_super_admin_google_sso ? 'emerald' : 'amber'}
        />
        <StatCard
          label="Recent Kiosk Unlocks"
          value={recentUnlocks.length}
          icon={Unlock}
          accent={recentUnlocks.length > 0 ? 'amber' : 'emerald'}
        />
        <StatCard
          label="Stranded Legacy TVs"
          value={data?.screens_without_secret ?? 0}
          icon={Tv}
          accent={data?.screens_without_secret === 0 ? 'emerald' : 'rose'}
        />
      </div>

      {/* Security Policies, Master Maintenance PIN & Dynamic Challenge-Response */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
        {/* Google SSO Policy Card */}
        <div className="flex flex-col justify-between rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Globe className="size-5 text-primary" />
                <h3 className="text-base font-semibold text-foreground">Super Admin Auth Policy</h3>
              </div>
              <span
                className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                  data?.require_super_admin_google_sso
                    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
                    : 'bg-amber-500/10 text-amber-700 dark:text-amber-400'
                }`}
              >
                {data?.require_super_admin_google_sso ? 'Enforced' : 'Optional'}
              </span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground leading-relaxed">
              When enforced, password login attempts by super administrators will be rejected with an immediate 403 Forbidden.
              Operators must sign in using verified Google Enterprise accounts.
            </p>
          </div>

          <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
            <span className="text-xs font-medium text-foreground">Enforce Google SSO</span>
            <button
              onClick={() => policyMutation.mutate(!data?.require_super_admin_google_sso)}
              disabled={policyMutation.isPending}
              className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                data?.require_super_admin_google_sso
                  ? 'border border-border bg-card text-foreground hover:bg-muted'
                  : 'bg-primary text-primary-foreground hover:bg-primary/90'
              }`}
            >
              {data?.require_super_admin_google_sso ? 'Disable' : 'Enforce'}
            </button>
          </div>
        </div>

        {/* Master Maintenance PIN Card with Audited Reveal */}
        <div className="flex flex-col justify-between rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Key className="size-5 text-amber-500" />
                <h3 className="text-base font-semibold text-foreground">Master Fleet Maintenance PIN</h3>
              </div>
              <span className="rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-400">
                Level-2 Guarded
              </span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground leading-relaxed">
              Universal door bypass code used on remote d-pads when screens lack an override PIN.
              Masked by default to protect display integrity. Every reveal is logged to the audit trail.
            </p>
          </div>

          <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">Master PIN:</span>
              <span className="font-mono text-base font-bold tracking-widest text-foreground">
                {revealedPin || '••••'}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => {
                  setNewPinValue('')
                  setRotatePinModalOpen(true)
                }}
                className="flex items-center gap-1.5 rounded-xl border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
              >
                <KeyRound className="size-3.5 text-amber-500" />
                Rotate PIN
              </button>

              {revealedPin ? (
                <button
                  type="button"
                  onClick={() => setRevealedPin(null)}
                  className="flex items-center gap-1.5 rounded-xl border border-border bg-muted/60 px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
                >
                  <EyeOff className="size-3.5" />
                  Hide
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => setRevealModalOpen(true)}
                  className="flex items-center gap-1.5 rounded-xl bg-amber-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-amber-500"
                >
                  <Eye className="size-3.5" />
                  Unmask PIN
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Dynamic Challenge-Response Unlock Card */}
        <div className="flex flex-col justify-between rounded-2xl border border-border bg-card p-6 shadow-sm">
          <div>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldAlert className="size-5 text-indigo-500" />
                <h3 className="text-base font-semibold text-foreground">Dynamic Challenge Unlock</h3>
              </div>
              <span className="rounded-full border border-indigo-500/30 bg-indigo-500/10 px-2.5 py-0.5 text-xs font-semibold text-indigo-700 dark:text-indigo-400">
                Option 1.2 HMAC
              </span>
            </div>
            <p className="mt-2 text-xs text-muted-foreground leading-relaxed">
              Generate an ephemeral, single-use 6-digit emergency PIN for on-site TV maintenance. The TV computes HMAC-SHA256 from its device nonce without storing any permanent master credentials.
            </p>
          </div>

          <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
            <span className="text-xs text-muted-foreground">Zero static device secrets</span>
            <button
              onClick={() => {
                setChallengeResult(null)
                setChallengeModalOpen(true)
              }}
              className="flex items-center gap-1.5 rounded-xl bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-500 transition"
            >
              <KeyRound className="size-3.5" />
              Generate Code
            </button>
          </div>
        </div>
      </div>

      {/* Tabs for detailed tables */}
      <section className="space-y-4 rounded-2xl border border-border bg-card p-6 shadow-sm">
        <div className="flex flex-wrap items-center gap-2 border-b border-border pb-3">
          <button
            onClick={() => setActiveTab('admins')}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              activeTab === 'admins' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Super Administrators ({superAdmins.length})
          </button>
          <button
            onClick={() => setActiveTab('sign_ins')}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              activeTab === 'sign_ins' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Recent Sign-Ins ({recentSignIns.length})
          </button>
          <button
            onClick={() => setActiveTab('kiosk_unlocks')}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              activeTab === 'kiosk_unlocks' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Kiosk Unlock Events ({recentUnlocks.length})
          </button>
          <button
            onClick={() => setActiveTab('act_as')}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition ${
              activeTab === 'act_as' ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:text-foreground'
            }`}
          >
            Workspace Act-As History ({actAsEvents.length})
          </button>
        </div>

        {/* Tab 1: Super Administrators */}
        {activeTab === 'admins' && (
          <div className="space-y-4">
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-1">
              <div>
                <h4 className="text-sm font-semibold text-foreground">Platform Operators & Root Administrators</h4>
                <p className="text-xs text-muted-foreground">
                  Super administrators hold universal platform governance, tenant approvals, and fleet control authority.
                </p>
              </div>
              <button
                type="button"
                onClick={() => setAddAdminModalOpen(true)}
                className="flex items-center gap-1.5 rounded-xl bg-primary px-3.5 py-2 text-xs font-semibold text-primary-foreground shadow hover:bg-primary/90 transition"
              >
                <UserPlus className="size-3.5" />
                Add Super Administrator
              </button>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full min-w-[700px] text-sm">
                <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wider text-muted-foreground">
                  <tr>
                    <th className="p-3 pl-4 text-left">Admin User</th>
                    <th className="p-3 text-left">Email</th>
                    <th className="p-3 text-left">Auth Method</th>
                    <th className="p-3 text-left">Last Login</th>
                    <th className="p-3 text-left">Token Version</th>
                    <th className="p-3 pr-4 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border">
                  {superAdmins.map((admin) => (
                    <tr key={admin.id} className="hover:bg-muted/20">
                      <td className="p-3 pl-4 font-medium text-foreground">
                        <div className="flex items-center gap-2">
                          <Shield className="size-4 text-primary" />
                          <span>{admin.username}</span>
                          {admin.full_name && <span className="text-xs text-muted-foreground">({admin.full_name})</span>}
                          {admin.is_root_admin && (
                            <span className="inline-flex items-center gap-1 rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-semibold text-amber-700 dark:text-amber-400">
                              <Crown className="size-3" />
                              Root Admin
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="p-3 text-xs text-foreground">{admin.email || '—'}</td>
                      <td className="p-3">
                        <span className="rounded-md border border-border bg-muted px-2 py-0.5 text-xs capitalize text-foreground">
                          {admin.auth_provider === 'google' ? 'Google SSO' : admin.auth_provider}
                        </span>
                      </td>
                      <td className="p-3 text-xs text-muted-foreground">
                        {admin.last_login_at ? new Date(admin.last_login_at).toLocaleString() : 'Never'}
                      </td>
                      <td className="p-3 font-mono text-xs text-muted-foreground">v{admin.token_version}</td>
                      <td className="p-3 pr-4 text-right">
                        <div className="inline-flex items-center gap-2">
                          <button
                            onClick={async () => {
                              const res = await confirm({
                                title: `Revoke active sessions for ${admin.username}?`,
                                description: `This will increment ${admin.username}'s session token version and immediately invalidate all active web browsers and sessions. They will be forced to log in again.`,
                                confirmLabel: 'Sign Out Everywhere',
                                destructive: true,
                              })
                              if (res) signOutMutation.mutate(admin.id)
                            }}
                            disabled={signOutMutation.isPending}
                            title="Invalidates all active tokens by bumping token_version"
                            className="inline-flex items-center gap-1 rounded-lg border border-border bg-muted/40 px-2.5 py-1 text-xs font-medium text-foreground hover:bg-muted disabled:opacity-50"
                          >
                            <LogOut className="size-3" />
                            Sign Out
                          </button>

                          {admin.is_root_admin ? (
                            <span className="text-[11px] font-medium text-muted-foreground italic px-1">Protected</span>
                          ) : data?.current_user_is_root_admin ? (
                            <button
                              onClick={async () => {
                                const res = await confirm({
                                  title: `Remove Super Administrator ${admin.username}?`,
                                  description: `This will permanently revoke all super administrator permissions and invalidate all active sessions for ${admin.email || admin.username}.`,
                                  confirmLabel: 'Remove Administrator',
                                  destructive: true,
                                })
                                if (res) removeAdminMutation.mutate(admin.id)
                              }}
                              disabled={removeAdminMutation.isPending}
                              title="Revoke super admin access"
                              className="inline-flex items-center gap-1 rounded-lg border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-xs font-medium text-rose-700 dark:text-rose-400 hover:bg-rose-500/20 disabled:opacity-50"
                            >
                              <Trash2 className="size-3" />
                              Remove
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Tab 2: Recent Sign-Ins */}
        {activeTab === 'sign_ins' && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[700px] text-sm">
              <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-3 pl-4 text-left">Timestamp</th>
                  <th className="p-3 text-left">Attempted User</th>
                  <th className="p-3 text-left">Method</th>
                  <th className="p-3 text-left">Result</th>
                  <th className="p-3 text-left">IP Address</th>
                  <th className="p-3 pr-4 text-left">User Agent</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {recentSignIns.map((evt) => (
                  <tr key={evt.id} className="hover:bg-muted/20">
                    <td className="p-3 pl-4 text-xs text-muted-foreground">
                      {evt.occurred_at ? new Date(evt.occurred_at).toLocaleString() : '—'}
                    </td>
                    <td className="p-3 font-medium text-foreground">{evt.attempted_username}</td>
                    <td className="p-3 text-xs capitalize text-muted-foreground">{evt.method}</td>
                    <td className="p-3">
                      {evt.success ? (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400">
                          <CheckCircle className="size-3.5" />
                          Success
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-rose-700 dark:text-rose-400">
                          <AlertTriangle className="size-3.5" />
                          Failed
                        </span>
                      )}
                    </td>
                    <td className="p-3 font-mono text-xs text-foreground">{evt.ip || '—'}</td>
                    <td className="p-3 pr-4 font-mono text-[11px] text-muted-foreground truncate max-w-[200px]" title={evt.user_agent || ''}>
                      {evt.user_agent || '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Tab 3: Kiosk Unlocks */}
        {activeTab === 'kiosk_unlocks' && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[600px] text-sm">
              <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-3 pl-4 text-left">Timestamp</th>
                  <th className="p-3 text-left">Screen ID</th>
                  <th className="p-3 text-left">Organization ID</th>
                  <th className="p-3 text-left">Method</th>
                  <th className="p-3 pr-4 text-left">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {recentUnlocks.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="p-8 text-center text-xs text-muted-foreground">
                      No kiosk unlock events recorded.
                    </td>
                  </tr>
                ) : (
                  recentUnlocks.map((u) => (
                    <tr key={u.id} className="hover:bg-muted/20">
                      <td className="p-3 pl-4 text-xs text-muted-foreground">
                        {u.occurred_at ? new Date(u.occurred_at).toLocaleString() : '—'}
                      </td>
                      <td className="p-3 font-mono text-xs text-foreground">Screen #{u.screen_id}</td>
                      <td className="p-3 font-mono text-xs text-foreground">Org #{u.organization_id}</td>
                      <td className="p-3 text-xs capitalize text-foreground">{u.method}</td>
                      <td className="p-3 pr-4">
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400">
                          <CheckCircle className="size-3.5" />
                          Unlocked
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}

        {/* Tab 4: Act-As Activity */}
        {activeTab === 'act_as' && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[600px] text-sm">
              <thead className="border-b border-border bg-muted/30 text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-3 pl-4 text-left">Timestamp</th>
                  <th className="p-3 text-left">Super Admin Actor</th>
                  <th className="p-3 text-left">Target Tenant</th>
                  <th className="p-3 text-left">Action</th>
                  <th className="p-3 pr-4 text-left">Client IP</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {actAsEvents.length === 0 ? (
                  <tr>
                    <td colSpan={5} className="p-8 text-center text-xs text-muted-foreground">
                      No act-as workspace sessions recorded.
                    </td>
                  </tr>
                ) : (
                  actAsEvents.map((a) => (
                    <tr key={a.id} className="hover:bg-muted/20">
                      <td className="p-3 pl-4 text-xs text-muted-foreground">
                        {new Date(a.occurred_at).toLocaleString()}
                      </td>
                      <td className="p-3 font-medium text-foreground">{a.actor_username}</td>
                      <td className="p-3 font-mono text-xs text-foreground">Tenant #{a.target_org_id}</td>
                      <td className="p-3">
                        <span className="rounded bg-muted px-2 py-0.5 font-mono text-xs text-foreground">
                          {a.action}
                        </span>
                      </td>
                      <td className="p-3 pr-4 font-mono text-xs text-muted-foreground">{a.ip || '—'}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Reveal PIN Modal (Level-2 with Reason) */}
      {revealModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center gap-2">
              <Key className="size-5 text-amber-500" />
              <h3 className="text-base font-semibold text-foreground">Audited Maintenance PIN Reveal</h3>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Unmasking the master fleet maintenance PIN requires an operator audit reason. This action is permanently recorded.
            </p>

            <div className="mt-4">
              <label className="text-xs font-medium text-foreground">
                Operational Reason <span className="text-rose-500">* (Level-2 Guard)</span>
              </label>
              <textarea
                value={revealReason}
                onChange={(e) => setRevealReason(e.target.value)}
                placeholder="State field technician ticket number or troubleshooting context..."
                rows={3}
                className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-3 text-xs text-foreground placeholder:text-muted-foreground focus:border-amber-500 focus:outline-none"
              />
            </div>

            <div className="mt-6 flex justify-end gap-3">
              <button
                onClick={() => {
                  setRevealModalOpen(false)
                  setRevealReason('')
                }}
                className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
              >
                Cancel
              </button>
              <button
                onClick={() => revealMutation.mutate(revealReason)}
                disabled={!revealReason.trim() || revealMutation.isPending}
                className="rounded-xl bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-50"
              >
                {revealMutation.isPending ? 'Logging…' : 'Confirm & Reveal'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Rotate / Set Master Fleet Maintenance PIN Modal */}
      {rotatePinModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center gap-2">
              <KeyRound className="size-5 text-amber-500" />
              <h3 className="text-base font-semibold text-foreground">Rotate Master Maintenance PIN</h3>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Configure the 4-digit master maintenance PIN used across all fleet screens that lack a tenant-specific PIN.
            </p>

            <div className="mt-5 flex flex-col items-center gap-3">
              <label className="text-xs font-medium text-foreground">
                Enter New 4-Digit Numeric PIN
              </label>
              <input
                value={newPinValue}
                onChange={(e) => setNewPinValue(e.target.value.replace(/\D/g, '').slice(0, 4))}
                placeholder="••••"
                inputMode="numeric"
                maxLength={4}
                className="w-48 rounded-xl border border-border bg-muted px-4 py-2.5 text-center font-mono text-2xl tracking-[0.4em] text-foreground placeholder:tracking-normal placeholder:text-muted-foreground/40 focus:border-amber-500 focus:outline-none"
              />
              <span className="text-[11px] text-muted-foreground">
                Must be exactly 4 numeric digits (0000–9999)
              </span>
            </div>

            <div className="mt-6 flex items-center justify-between border-t border-border pt-4">
              <button
                type="button"
                onClick={async () => {
                  const res = await confirm({
                    title: 'Clear Master Maintenance PIN?',
                    description: 'Removing the master PIN means screens without an individual PIN can only be serviced via remote unlock or factory reset.',
                    confirmLabel: 'Clear Master PIN',
                    destructive: true,
                  })
                  if (res) setPinMutation.mutate('')
                }}
                disabled={setPinMutation.isPending}
                className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs font-medium text-rose-700 dark:text-rose-400 hover:bg-rose-500/20"
              >
                Clear Master PIN
              </button>

              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setRotatePinModalOpen(false)
                    setNewPinValue('')
                  }}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => setPinMutation.mutate(newPinValue)}
                  disabled={!/^\d{4}$/.test(newPinValue) || setPinMutation.isPending}
                  className="rounded-xl bg-amber-600 px-4 py-2 text-xs font-semibold text-white hover:bg-amber-500 disabled:opacity-40"
                >
                  {setPinMutation.isPending ? 'Saving…' : 'Save New PIN'}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Dynamic Challenge-Response Modal (Option 1.2) */}
      {challengeModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center gap-2">
              <ShieldAlert className="size-5 text-indigo-500" />
              <h3 className="text-base font-semibold text-foreground">Emergency Dynamic Challenge Generator</h3>
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              Enter the TV&apos;s Device ID and the 6-character Nonce displayed on screen. A single-use HMAC code will be generated for on-site technician access.
            </p>

            {challengeResult ? (
              <div className="mt-5 space-y-4 rounded-xl border border-indigo-500/30 bg-indigo-500/10 p-4 text-center">
                <span className="text-xs font-medium text-indigo-700 dark:text-indigo-300">Generated 6-Digit Single-Use Bypass Code</span>
                <div className="flex items-center justify-center gap-3">
                  <span className="font-mono text-3xl font-extrabold tracking-[0.3em] text-foreground">
                    {challengeResult.challenge_code || challengeResult.response_code}
                  </span>
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(challengeResult.challenge_code || challengeResult.response_code || '')
                      setCopiedChallenge(true)
                      setTimeout(() => setCopiedChallenge(false), 2000)
                    }}
                    className="rounded-lg border border-border bg-card p-2 text-muted-foreground hover:text-foreground"
                    title="Copy Code"
                  >
                    {copiedChallenge ? <Check className="size-4 text-emerald-500" /> : <Copy className="size-4" />}
                  </button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Valid for {challengeResult.validity_window_seconds ?? 300}s (expires: {new Date(challengeResult.expires_at).toLocaleTimeString()})
                </p>

                <div className="pt-2">
                  <button
                    onClick={() => {
                      setChallengeModalOpen(false)
                      setChallengeResult(null)
                      setChallengeNonce('')
                    }}
                    className="w-full rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500"
                  >
                    Done
                  </button>
                </div>
              </div>
            ) : (
              <div className="mt-4 space-y-3">
                <div>
                  <label className="text-xs font-medium text-foreground">TV Device ID</label>
                  <input
                    type="text"
                    value={challengeDeviceId}
                    onChange={(e) => setChallengeDeviceId(e.target.value)}
                    placeholder="e.g. tv_abc12345"
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 font-mono text-xs text-foreground placeholder:text-muted-foreground focus:border-indigo-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-foreground">Screen Nonce</label>
                  <input
                    type="text"
                    value={challengeNonce}
                    onChange={(e) => setChallengeNonce(e.target.value.toUpperCase())}
                    placeholder="e.g. A1B2C3"
                    maxLength={16}
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 font-mono text-xs uppercase tracking-wider text-foreground placeholder:text-muted-foreground focus:border-indigo-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-foreground">Super Admin Password (Re-authentication)</label>
                  <input
                    type="password"
                    value={challengePassword}
                    onChange={(e) => setChallengePassword(e.target.value)}
                    placeholder="Enter your operator password"
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-indigo-500 focus:outline-none"
                  />
                </div>

                <div>
                  <label className="text-xs font-medium text-foreground">
                    Operational Reason <span className="text-rose-500">*</span>
                  </label>
                  <textarea
                    value={challengeReason}
                    onChange={(e) => setChallengeReason(e.target.value)}
                    placeholder="State on-site technician ticket, screen location, or incident..."
                    rows={2}
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-indigo-500 focus:outline-none"
                  />
                </div>

                <div className="mt-6 flex justify-end gap-3 pt-2">
                  <button
                    onClick={() => {
                      setChallengeModalOpen(false)
                      setChallengePassword('')
                    }}
                    className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={() => challengeMutation.mutate()}
                    disabled={
                      !challengeDeviceId.trim() ||
                      !challengeNonce.trim() ||
                      !challengePassword ||
                      !challengeReason.trim() ||
                      challengeMutation.isPending
                    }
                    className="rounded-xl bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-50"
                  >
                    {challengeMutation.isPending ? 'Calculating…' : 'Generate Emergency PIN'}
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Add Super Administrator Modal */}
      {addAdminModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
            <div className="flex items-center justify-between border-b border-border pb-3">
              <div className="flex items-center gap-2">
                <UserPlus className="size-5 text-primary" />
                <h3 className="text-base font-semibold text-foreground">Add Super Administrator</h3>
              </div>
              <button
                type="button"
                onClick={() => setAddAdminModalOpen(false)}
                className="text-xs text-muted-foreground hover:text-foreground"
              >
                ✕
              </button>
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault()
                createAdminMutation.mutate()
              }}
              className="mt-4 space-y-4"
            >
              <div>
                <label className="text-xs font-semibold text-foreground">
                  Admin Email Address <span className="text-rose-500">*</span>
                </label>
                <input
                  type="email"
                  required
                  value={newAdminEmail}
                  onChange={(e) => setNewAdminEmail(e.target.value)}
                  placeholder="e.g. operator@company.com"
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
                />
                <p className="mt-1 text-[11px] text-muted-foreground">
                  Google Workspace or Gmail account for platform access.
                </p>
              </div>

              <div>
                <label className="text-xs font-semibold text-foreground">Full Name (Optional)</label>
                <input
                  type="text"
                  value={newAdminFullName}
                  onChange={(e) => setNewAdminFullName(e.target.value)}
                  placeholder="e.g. John Doe"
                  className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
                />
              </div>

              <div>
                <label className="text-xs font-semibold text-foreground">Authentication Method</label>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setNewAdminAuthMethod('google')}
                    className={`rounded-xl border p-2.5 text-left text-xs transition ${
                      newAdminAuthMethod === 'google'
                        ? 'border-primary bg-primary/10 font-semibold text-primary'
                        : 'border-border bg-card text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <div className="font-medium">Google SSO Only</div>
                    <div className="text-[10px] opacity-75">No password stored</div>
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewAdminAuthMethod('password_and_google')}
                    className={`rounded-xl border p-2.5 text-left text-xs transition ${
                      newAdminAuthMethod === 'password_and_google'
                        ? 'border-primary bg-primary/10 font-semibold text-primary'
                        : 'border-border bg-card text-muted-foreground hover:text-foreground'
                    }`}
                  >
                    <div className="font-medium">Password + Google</div>
                    <div className="text-[10px] opacity-75">Hybrid credentials</div>
                  </button>
                </div>
              </div>

              {newAdminAuthMethod === 'password_and_google' && (
                <div>
                  <label className="text-xs font-semibold text-foreground">
                    Password <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="password"
                    required
                    minLength={8}
                    value={newAdminPassword}
                    onChange={(e) => setNewAdminPassword(e.target.value)}
                    placeholder="Minimum 8 characters"
                    className="mt-1 w-full rounded-xl border border-border bg-muted/40 p-2.5 text-xs text-foreground placeholder:text-muted-foreground focus:border-primary focus:outline-none"
                  />
                </div>
              )}

              <div className="mt-6 flex justify-end gap-2 border-t border-border pt-4">
                <button
                  type="button"
                  onClick={() => setAddAdminModalOpen(false)}
                  className="rounded-xl border border-border px-4 py-2 text-xs font-medium text-foreground hover:bg-muted"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={
                    !newAdminEmail.trim() ||
                    (newAdminAuthMethod === 'password_and_google' && (!newAdminPassword || newAdminPassword.length < 8)) ||
                    createAdminMutation.isPending
                  }
                  className="rounded-xl bg-primary px-4 py-2 text-xs font-semibold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
                >
                  {createAdminMutation.isPending ? 'Provisioning…' : 'Create Administrator'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}

