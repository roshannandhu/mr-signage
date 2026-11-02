import type { TenantSummary } from './types'

export interface TenantHealth {
  score: number
  tone: 'emerald' | 'amber' | 'rose'
  reasons: string[]
}

/**
 * Computes a 0-100 health score for a tenant workspace.
 * Reflects screen online ratio, subscription status, and storage pressure.
 */
export function computeTenantHealth(t: TenantSummary): TenantHealth {
  if (t.status === 'suspended') {
    return { score: 10, tone: 'rose', reasons: ['Workspace is suspended'] }
  }
  if (t.deleted_at) {
    return { score: 0, tone: 'rose', reasons: ['Workspace scheduled for deletion'] }
  }

  let score = 0
  const reasons: string[] = []

  // 1. Screens online (up to 40 pts)
  if (t.screens_count === 0) {
    score += 35
    reasons.push('No screens registered yet')
  } else {
    const ratio = t.online_screens_count / t.screens_count
    const pts = Math.round(ratio * 40)
    score += pts
    if (ratio === 1) {
      reasons.push('All screens online')
    } else {
      reasons.push(`${t.screens_count - t.online_screens_count} of ${t.screens_count} screens offline`)
    }
  }

  // 2. Subscription state (up to 35 pts)
  if (t.subscription_state === 'active') {
    score += 35
    reasons.push('Subscription running active')
  } else if (t.subscription_state === 'grace') {
    score += 15
    reasons.push('In payment grace period')
  } else if (t.subscription_state === 'expired') {
    score += 0
    reasons.push('Plan has expired')
  } else {
    score += 25
  }

  // 3. Storage & quota usage (up to 25 pts)
  if (t.storage_quota_bytes && t.storage_quota_bytes > 0) {
    const usage = t.storage_used_bytes / t.storage_quota_bytes
    if (usage > 0.9) {
      score += 5
      reasons.push(`Storage pressure (${Math.round(usage * 100)}% used)`)
    } else {
      score += 25
      reasons.push('Storage within limit')
    }
  } else {
    score += 25
  }

  score = Math.max(0, Math.min(100, score))
  const tone = score >= 75 ? 'emerald' : score >= 45 ? 'amber' : 'rose'

  return { score, tone, reasons }
}
