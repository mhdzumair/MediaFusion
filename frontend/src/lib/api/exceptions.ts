/**
 * Exception Tracking API client.
 *
 * Uses the same adminGet / adminDelete pattern from admin.ts
 * to communicate with /api/v1/admin/exceptions endpoints.
 */

import { apiClient } from './client'

const ADMIN_BASE = '/api/v1/admin'

// ============================================
// Types
// ============================================

export interface ExceptionSummary {
  fingerprint: string
  type: string
  message: string
  count: number
  first_seen: string
  last_seen: string
  source: string
}

export interface ExceptionDetail extends ExceptionSummary {
  traceback: string
}

export interface ExceptionListResponse {
  items: ExceptionSummary[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface ExceptionListParams {
  page?: number
  per_page?: number
  exception_type?: string
}

export interface ExceptionStatusResponse {
  enabled: boolean
  ttl_seconds: number
  max_entries: number
  total_tracked: number
}

export interface ExceptionClearResponse {
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

export const exceptionsApi = {
  /** Get the status of exception tracking (enabled, TTL, count). */
  getStatus: () => adminGet<ExceptionStatusResponse>('/exceptions/status'),

  /** List tracked exceptions (paginated, most recent first). */
  list: (params: ExceptionListParams = {}) => adminGet<ExceptionListResponse>(`/exceptions${buildQueryString(params)}`),

  /** Get full detail (including traceback) for a single exception. */
  getDetail: (fingerprint: string) => adminGet<ExceptionDetail>(`/exceptions/${fingerprint}`),

  /** Clear all tracked exceptions. */
  clearAll: () => adminDelete<ExceptionClearResponse>('/exceptions'),

  /** Clear a single tracked exception by fingerprint. */
  clear: (fingerprint: string) => adminDelete<ExceptionClearResponse>(`/exceptions/${fingerprint}`),
}
