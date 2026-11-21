import { useQuery } from '@tanstack/react-query'
import { analyticsApi, dashboardApi } from '../services/api'
import type { BreakdownKind } from '../types/api'
import { keys } from './useUrls'

// The worker flushes roughly every 5 s, so polling every 15 s keeps numbers current without hammering the API.
const POLL_MS = 15_000

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10)
}

export function dateRange(days: number, now: Date = new Date()): { from: string; to: string } {
  const to = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()))
  const from = new Date(to.getTime() - (days - 1) * 86_400_000)
  return { from: isoDay(from), to: isoDay(to) }
}

export function useDashboardSummary() {
  return useQuery({ queryKey: keys.dashboard, queryFn: dashboardApi.summary, refetchInterval: POLL_MS })
}

export function useOverview(code: string) {
  return useQuery({
    queryKey: [...keys.analytics(code), 'overview'],
    queryFn: () => analyticsApi.overview(code),
    refetchInterval: POLL_MS,
  })
}

export function useDaily(code: string, days: number) {
  const { from, to } = dateRange(days)
  return useQuery({
    queryKey: [...keys.analytics(code), 'daily', from, to],
    queryFn: () => analyticsApi.daily(code, from, to),
    refetchInterval: POLL_MS,
  })
}

export function useBreakdown(code: string, kind: BreakdownKind) {
  return useQuery({
    queryKey: [...keys.analytics(code), kind],
    queryFn: () => analyticsApi.breakdown(code, kind),
    refetchInterval: POLL_MS,
  })
}
