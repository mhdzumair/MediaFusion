/**
 * Request Metrics API client.
 *
 * Uses the same adminGet / adminDelete pattern from the exceptions API
 * to communicate with /api/v1/admin/request-metrics endpoints.
 */

import { apiClient } from './client'

const ADMIN_BASE = '/api/v1/admin'

// ============================================
// Types
// ============================================

export interface EndpointStatsSummary {
  endpoint_key: string
  method: string
  route: string
  total_requests: number
  avg_time: number
  min_time: number
  max_time: number
  error_count: number
  status_2xx: number
  status_3xx: number
  status_4xx: number
  status_5xx: number
  unique_visitors: number
  last_seen: string
}

export interface EndpointStatsDetail extends EndpointStatsSummary {
  p50: number
  p95: number
  p99: number
}

export interface EndpointStatsListResponse {
  items: EndpointStatsSummary[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface EndpointStatsListParams {
  page?: number
  per_page?: number
  sort_by?: string
  sort_order?: string
}

export interface RecentRequestItem {
  request_id: string
  method: string
  path: string
  route_template: string
  status_code: number
  process_time: number
  timestamp: string
}

export interface RecentRequestsListResponse {
  items: RecentRequestItem[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface RecentRequestsListParams {
  page?: number
  per_page?: number
  method?: string
  status_code?: number
  route?: string
}

export interface RequestMetricsStatusResponse {
  enabled: boolean
  ttl_seconds: number
  recent_ttl_seconds: number
  max_recent: number
  total_endpoints: number
  total_requests: number
  total_recent: number
  unique_visitors: number
}

export interface RequestMetricsClearResponse {
  cleared: number
  message: string
}

// ============================================
// Helpers
// ============================================

function buildQueryString<T extends object>(params: T): string {
  const searchParams = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) {
      searchParams.append(key, String(value))
    }
  }
  const query = searchParams.toString()
  return query ? `?${query}` : ''
}

async function adminGet<T>(endpoint: string): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'GET',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

async function adminDelete<T>(endpoint: string): Promise<T> {
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  const headers: HeadersInit = {
    'Content-Type': 'application/json',
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${ADMIN_BASE}${endpoint}`, {
    method: 'DELETE',
    headers,
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `HTTP error ${response.status}` }))
    throw new Error(error.detail || 'An error occurred')
  }

  return response.json()
}

// ============================================
// API
// ============================================

export const requestMetricsApi = {
  /** Get the status of request metrics tracking (enabled, TTL, counts). */
  getStatus: () => adminGet<RequestMetricsStatusResponse>('/request-metrics/status'),

  /** List aggregated endpoint stats (paginated). */
  listEndpoints: (params: EndpointStatsListParams = {}) =>
    adminGet<EndpointStatsListResponse>(`/request-metrics/endpoints${buildQueryString(params)}`),

  /** Get detailed stats for a specific endpoint including percentiles. */
  getEndpointDetail: (method: string, route: string) =>
    adminGet<EndpointStatsDetail>(`/request-metrics/endpoints/${method}${route}`),

  /** List recent individual requests (paginated). */
  listRecent: (params: RecentRequestsListParams = {}) =>
    adminGet<RecentRequestsListResponse>(`/request-metrics/recent${buildQueryString(params)}`),

  /** Clear all request metrics. */
  clearAll: () => adminDelete<RequestMetricsClearResponse>('/request-metrics'),
}
