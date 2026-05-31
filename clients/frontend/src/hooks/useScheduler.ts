import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { schedulerApi, scrapersApi, type SchedulerListParams } from '@/lib/api'

export const schedulerKeys = {
  all: ['scheduler'] as const,
  list: (params?: SchedulerListParams) => [...schedulerKeys.all, 'list', params] as const,
  stats: () => [...schedulerKeys.all, 'stats'] as const,
  detail: (jobId: string) => [...schedulerKeys.all, 'detail', jobId] as const,
  history: (jobId: string, limit?: number) => [...schedulerKeys.all, 'history', jobId, limit] as const,
  dmmHashlistStatus: () => [...schedulerKeys.all, 'dmm-hashlist-status'] as const,
  imdbDatasetConfig: () => [...schedulerKeys.all, 'imdb-dataset-config'] as const,
  imdbDatasetStatus: () => [...schedulerKeys.all, 'imdb-dataset-status'] as const,
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
 * Get DMM hashlist ingestion status/checkpoints (admin only)
 */
export function useDmmHashlistStatus() {
  return useQuery({
    queryKey: schedulerKeys.dmmHashlistStatus(),
    queryFn: () => scrapersApi.getDMMHashlistStatus(),
    staleTime: 30 * 1000,
    refetchInterval: 60 * 1000,
  })
}

/**
 * Manually run a scheduler job (admin only)
 * Queues the job for background execution
 */
export function useRunSchedulerJob() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({
      jobId,
      forceRun = false,
      payload,
    }: {
      jobId: string
      forceRun?: boolean
      payload?: Record<string, unknown>
    }) => schedulerApi.run(jobId, { forceRun, payload }),
    onSuccess: () => {
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

/**
 * Run full DMM ingestion loop once (admin only).
 */
export function useRunDmmHashlistFull() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (payload: {
      sync?: boolean
      reset_checkpoints?: boolean
      max_iterations?: number
      incremental_commits?: number
      backfill_commits?: number
    }) => scrapersApi.runDMMHashlistFull(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
      queryClient.invalidateQueries({ queryKey: schedulerKeys.dmmHashlistStatus() })
    },
  })
}

export function useImdbDatasetConfig() {
  return useQuery({
    queryKey: schedulerKeys.imdbDatasetConfig(),
    queryFn: () => scrapersApi.getImdbDatasetConfig(),
    staleTime: 30 * 1000,
  })
}

export function useImdbDatasetStatus(enabled = true) {
  return useQuery({
    queryKey: schedulerKeys.imdbDatasetStatus(),
    queryFn: () => scrapersApi.getImdbDatasetStatus(),
    enabled,
    staleTime: 5 * 1000,
    refetchInterval: (query) => {
      const phase = query.state.data?.phase
      if (phase && phase !== 'idle' && phase !== 'complete' && phase !== 'error') {
        return 3000
      }
      return 30 * 1000
    },
  })
}

export function useUpdateImdbDatasetConfig() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: scrapersApi.updateImdbDatasetConfig,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: schedulerKeys.imdbDatasetConfig() })
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
    },
  })
}

export function useRunImdbDatasetImport() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: scrapersApi.runImdbDatasetImport,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
      queryClient.invalidateQueries({ queryKey: schedulerKeys.imdbDatasetConfig() })
      queryClient.invalidateQueries({ queryKey: schedulerKeys.imdbDatasetStatus() })
    },
  })
}

export function useUpdateSchedulerJob() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ jobId, payload }: { jobId: string; payload: Parameters<typeof schedulerApi.update>[1] }) =>
      schedulerApi.update(jobId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: schedulerKeys.all })
    },
  })
}
