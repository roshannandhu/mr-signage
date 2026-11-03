import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import { useAuthStore } from './store'

/**
 * The tenant's plan feature flags, for deciding what to SHOW.
 *
 * The server is still what enforces a flag (require_feature) -- this only decides whether the
 * nav link, button or page exists at all. A feature the plan lacks is hidden outright, not shown
 * disabled with an upgrade hint: a tenant should not be shown capabilities their admin has not
 * turned on for them.
 *
 * Shares its cache (queryKey ['billing-summary']) with every page that already loads the plan
 * summary, so adding this to the nav costs no extra request on any page that also uses it.
 */
export function usePlanFeatures() {
  const token = useAuthStore((state) => state.token)
  const hasToken = Boolean(token || (typeof window !== 'undefined' && localStorage.getItem('token')))
  const summaryQuery = useQuery({
    queryKey: ['billing-summary'],
    queryFn: api.getBillingSummary,
    enabled: hasToken,
  })
  const flags = summaryQuery.data?.plan.feature_flags ?? {}
  // Unknown while loading reads as "has it", so the nav does not flash an item and then hide
  // it a moment later on every page load.
  const has = (key: string) => (summaryQuery.isLoading ? true : Boolean(flags[key]))
  return { has, isLoading: summaryQuery.isLoading, flags }
}
