/**
 * React Query hooks for exception tracking.
 */

import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { exceptionsApi, type ExceptionListParams } from '@/lib/api/exceptions'

export const exceptionKeys = {
  all: ['exceptions'] as const,
  status: () => [...exceptionKeys.all, 'status'] as const,
  list: (params?: ExceptionListParams) => [...exceptionKeys.all, 'list', params] as const,
  detail: (fingerprint: string) => [...exceptionKeys.all, 'detail', fingerprint] as const,
}

/** Fetch exception tracking status (enabled, TTL, total count). */
export function useExceptionStatus() {
  return useQuery({
    queryKey: exceptionKeys.status(),
    queryFn: () => exceptionsApi.getStatus(),
    staleTime: 30_000,
  })
}

/** List tracked exceptions with pagination. */
export function useExceptionList(params: ExceptionListParams = {}) {
  return useQuery({
    queryKey: exceptionKeys.list(params),
    queryFn: () => exceptionsApi.list(params),
    staleTime: 15_000,
    refetchInterval: 30_000,
  })
}

/** Fetch full detail for a single tracked exception. */
export function useExceptionDetail(fingerprint: string | null) {
  return useQuery({
    queryKey: exceptionKeys.detail(fingerprint ?? ''),
    queryFn: () => exceptionsApi.getDetail(fingerprint!),
    enabled: !!fingerprint,
    staleTime: 30_000,
  })
}

/** Clear a single exception by fingerprint. */
export function useClearException() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (fingerprint: string) => exceptionsApi.clear(fingerprint),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: exceptionKeys.all })
    },
  })
}

/** Clear all tracked exceptions. */
export function useClearAllExceptions() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: () => exceptionsApi.clearAll(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: exceptionKeys.all })
    },
  })
}
