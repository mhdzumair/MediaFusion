/**
 * React Query hooks for request metrics tracking.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  requestMetricsApi,
  type EndpointStatsListParams,
  type RecentRequestsListParams,
} from '@/lib/api/requestMetrics'

export const requestMetricsKeys = {
  all: ['requestMetrics'] as const,
  status: () => [...requestMetricsKeys.all, 'status'] as const,
  endpoints: (params?: EndpointStatsListParams) => [...requestMetricsKeys.all, 'endpoints', params] as const,
  endpointDetail: (method: string, route: string) =>
    [...requestMetricsKeys.all, 'endpointDetail', method, route] as const,
  recent: (params?: RecentRequestsListParams) => [...requestMetricsKeys.all, 'recent', params] as const,
}

/** Fetch request metrics tracking status (enabled, TTL, counts). */
export function useRequestMetricsStatus() {
  return useQuery({
    queryKey: requestMetricsKeys.status(),
    queryFn: () => requestMetricsApi.getStatus(),
    staleTime: 30_000,
  })
}

/** List aggregated endpoint stats with pagination. */
export function useEndpointStats(params: EndpointStatsListParams = {}) {
  return useQuery({
    queryKey: requestMetricsKeys.endpoints(params),
    queryFn: () => requestMetricsApi.listEndpoints(params),
    staleTime: 15_000,
    refetchInterval: 30_000,
  })
}

/** Fetch detailed stats for a specific endpoint including percentiles. */
export function useEndpointDetail(method: string | null, route: string | null) {
  return useQuery({
    queryKey: requestMetricsKeys.endpointDetail(method ?? '', route ?? ''),
    queryFn: () => requestMetricsApi.getEndpointDetail(method!, route!),
    enabled: !!method && !!route,
    staleTime: 15_000,
  })
}

/** List recent individual requests with pagination and filters. */
export function useRecentRequests(params: RecentRequestsListParams = {}) {
  return useQuery({
    queryKey: requestMetricsKeys.recent(params),
    queryFn: () => requestMetricsApi.listRecent(params),
    staleTime: 10_000,
    refetchInterval: 15_000,
  })
}

/** Clear all request metrics. */
export function useClearRequestMetrics() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => requestMetricsApi.clearAll(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: requestMetricsKeys.all })
    },
  })
}
