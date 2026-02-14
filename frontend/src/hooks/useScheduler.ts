import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { schedulerApi, type SchedulerListParams } from '@/lib/api'

export const schedulerKeys = {
  all: ['scheduler'] as const,
  list: (params?: SchedulerListParams) => [...schedulerKeys.all, 'list', params] as const,
  stats: () => [...schedulerKeys.all, 'stats'] as const,
  detail: (jobId: string) => [...schedulerKeys.all, 'detail', jobId] as const,
  history: (jobId: string, limit?: number) => [...schedulerKeys.all, 'history', jobId, limit] as const,
}

/**
 * List all scheduler jobs (admin only)
 */
export function useSchedulerJobs(params?: SchedulerListParams) {
  return useQuery({
    queryKey: schedulerKeys.list(params),
    queryFn: () => schedulerApi.list(params),
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // Refetch every minute
  })
}

/**
 * Get scheduler statistics (admin only)
 */
export function useSchedulerStats() {
  return useQuery({
    queryKey: schedulerKeys.stats(),
    queryFn: () => schedulerApi.getStats(),
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // Refetch every minute
  })
}

/**
 * Get a single scheduler job (admin only)
 */
export function useSchedulerJob(jobId: string | undefined) {
  return useQuery({
    queryKey: schedulerKeys.detail(jobId!),
    queryFn: () => schedulerApi.get(jobId!),
    enabled: !!jobId,
    staleTime: 30 * 1000,
  })
}

/**
 * Get job execution history (admin only)
 */
export function useSchedulerJobHistory(jobId: string | undefined, limit: number = 20) {
  return useQuery({
    queryKey: schedulerKeys.history(jobId!, limit),
    queryFn: () => schedulerApi.getHistory(jobId!, limit),
    enabled: !!jobId,
  })
}

/**
 * Manually run a scheduler job (admin only)
 * Queues the job for background execution
 */
export function useRunSchedulerJob() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (jobId: string) => schedulerApi.run(jobId),
    onSuccess: () => {
      // Invalidate scheduler data to refresh running status
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
    },
  })
}

/**
 * Run a scheduler job inline/directly (admin only)
 * WARNING: This runs synchronously in FastAPI process - use for testing only
 */
export function useRunSchedulerJobInline() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (jobId: string) => schedulerApi.runInline(jobId),
    onSuccess: () => {
      // Invalidate scheduler data to refresh running status
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
    },
  })
}
