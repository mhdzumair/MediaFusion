import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/lib/api'
import type { CacheStats, CacheKeysResponse, CacheValueResponse, ClearCacheResponse, DeleteKeyResponse } from '../types'

// Query keys
export const cacheQueryKeys = {
  all: ['cache'] as const,
  stats: () => [...cacheQueryKeys.all, 'stats'] as const,
  keys: (pattern: string, typeFilter: string, cacheCategory: string | null | undefined, cursor: string) =>
    [...cacheQueryKeys.all, 'keys', pattern, typeFilter, cacheCategory ?? '', cursor] as const,
  key: (key: string) => [...cacheQueryKeys.all, 'key', key] as const,
}

// Fetch cache statistics
export function useCacheStats() {
  return useQuery({
    queryKey: cacheQueryKeys.stats(),
    queryFn: async () => {
      const response = await apiClient.get<CacheStats>('/admin/cache/stats')
      return response
    },
    refetchInterval: 30000, // Refresh every 30 seconds
  })
}

// Fetch one page of cache keys (cursor-based SCAN pagination)
export function useCacheKeys(pattern: string, typeFilter: string = '', cacheCategory?: string | null, cursor = '0') {
  return useQuery({
    queryKey: cacheQueryKeys.keys(pattern, typeFilter, cacheCategory, cursor),
    queryFn: async () => {
      const params = new URLSearchParams({
        pattern: cacheCategory ? '*' : pattern || '*',
        cursor: String(cursor),
        count: '50',
      })
      if (typeFilter && typeFilter !== 'all') {
        params.append('type_filter', typeFilter)
      }
      if (cacheCategory) {
        params.append('cache_category', cacheCategory)
      }
      const response = await apiClient.get<CacheKeysResponse>(`/admin/cache/keys?${params.toString()}`)
      return response
    },
    enabled: Boolean(cacheCategory || (pattern && pattern.length > 0)),
  })
}

// Fetch single key value
export function useCacheKeyValue(key: string | null) {
  return useQuery({
    queryKey: cacheQueryKeys.key(key || ''),
    queryFn: async () => {
      if (!key) throw new Error('No key provided')
      const response = await apiClient.get<CacheValueResponse>(`/admin/cache/key/${encodeURIComponent(key)}`)
      return response
    },
    enabled: !!key,
    staleTime: 0, // Always fetch fresh data
  })
}

// Clear cache mutation
export function useClearCache() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ type, pattern }: { type: string; pattern?: string }) => {
      const body: { type: string; pattern?: string } = { type }
      if (pattern !== undefined) {
        body.pattern = pattern
      }
      const response = await apiClient.post<ClearCacheResponse>('/admin/cache/clear', body)
      return response
    },
    onSuccess: () => {
      // Invalidate all cache queries
      queryClient.invalidateQueries({ queryKey: cacheQueryKeys.all })
    },
  })
}

// Delete single key mutation
export function useDeleteCacheKey() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (key: string) => {
      const response = await apiClient.delete<DeleteKeyResponse>(`/admin/cache/key/${encodeURIComponent(key)}`)
      return { ...response, key }
    },
    onSuccess: (_, deletedKey) => {
      // Invalidate stats
      queryClient.invalidateQueries({ queryKey: cacheQueryKeys.stats() })
      // Remove from cached keys lists
      queryClient.setQueriesData<CacheKeysResponse>({ queryKey: [...cacheQueryKeys.all, 'keys'] }, (old) => {
        if (!old) return old
        return {
          ...old,
          keys: old.keys.filter((k) => k.key !== deletedKey),
          total: old.total - 1,
        }
      })
    },
  })
}

// Delete item from complex type mutation
export interface DeleteItemParams {
  key: string
  field?: string // For hash
  member?: string // For set/zset
  value?: string // For list
  index?: number // For list
}

export function useDeleteCacheItem() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({ key, ...body }: DeleteItemParams) => {
      // Use fetch directly since apiClient.delete doesn't support body
      const token = apiClient.getAccessToken()
      const response = await fetch(`/api/v1/admin/cache/key/${encodeURIComponent(key)}/item`, {
        method: 'DELETE',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify(body),
      })

      if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Failed to delete item' }))
        throw new Error(error.detail || 'Failed to delete item')
      }

      const result = await response.json()
      return { ...result, key }
    },
    onSuccess: (_, { key }) => {
      // Invalidate the specific key's value
      queryClient.invalidateQueries({ queryKey: cacheQueryKeys.key(key) })
    },
  })
}

// Fetch image for cache key
export async function fetchCacheImage(key: string): Promise<string> {
  const token = apiClient.getAccessToken()
  if (!token) {
    throw new Error('No access token available')
  }

  const response = await fetch(`/api/v1/admin/cache/image/${encodeURIComponent(key)}`, {
    headers: {
      Authorization: `Bearer ${token}`,
    },
  })

  if (!response.ok) {
    throw new Error('Failed to fetch image')
  }

  const blob = await response.blob()
  return URL.createObjectURL(blob)
}
