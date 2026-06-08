import { useMemo } from 'react'
import { useQuery, useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import {
  schedulerApi,
  scrapersApi,
  type SchedulerJobInfo,
  type SchedulerJobsResponse,
  type SchedulerListParams,
  type SchedulerStreamParams,
} from '@/lib/api'
import { useEventStreamConnection } from './useEventStreamConnection'

export const schedulerKeys = {
  all: ['scheduler'] as const,
  list: (params?: SchedulerListParams) => [...schedulerKeys.all, 'list', params] as const,
  stats: () => [...schedulerKeys.all, 'stats'] as const,
  detail: (jobId: string) => [...schedulerKeys.all, 'detail', jobId] as const,
  history: (jobId: string, limit?: number) => [...schedulerKeys.all, 'history', jobId, limit] as const,
  logs: (jobId: string, limit?: number) => [...schedulerKeys.all, 'logs', jobId, limit] as const,
  dmmHashlistStatus: () => [...schedulerKeys.all, 'dmm-hashlist-status'] as const,
  imdbDatasetConfig: () => [...schedulerKeys.all, 'imdb-dataset-config'] as const,
  imdbDatasetStatus: () => [...schedulerKeys.all, 'imdb-dataset-status'] as const,
}

type StreamQueryOptions = {
  streamEnabled?: boolean
}

function patchSchedulerJobInCache(queryClient: QueryClient, updatedJob: SchedulerJobInfo) {
  queryClient.setQueriesData<SchedulerJobsResponse>(
    {
      queryKey: schedulerKeys.all,
      predicate: (query) => query.queryKey[1] === 'list',
    },
    (old) => {
      if (!old?.jobs) {
        return old
      }
      return {
        ...old,
        jobs: old.jobs.map((job) => (job.id === updatedJob.id ? { ...job, ...updatedJob } : job)),
      }
    },
  )
}

function useSchedulerEventStream(options: { enabled: boolean; streamParams: SchedulerStreamParams }) {
  const queryClient = useQueryClient()
  const listParams = useMemo(
    () => ({
      category: options.streamParams.category,
      enabled_only: options.streamParams.enabled_only,
    }),
    [options.streamParams.category, options.streamParams.enabled_only],
  )
  const streamKey = useMemo(() => JSON.stringify(options.streamParams), [options.streamParams])

  const connect = useMemo(
    () => async (signal: AbortSignal, onConnected: () => void) => {
      await schedulerApi.connectStream(
        options.streamParams,
        (snapshot) => {
          onConnected()
          queryClient.setQueryData(schedulerKeys.list(listParams), snapshot.list)
          queryClient.setQueryData(schedulerKeys.stats(), snapshot.stats)
        },
        () => {},
        signal,
      )
    },
    [listParams, options.streamParams, queryClient],
  )

  return useEventStreamConnection({
    enabled: options.enabled,
    streamKey,
    connect,
  })
}

/**
 * List all scheduler jobs (admin only)
 */
export function useSchedulerJobs(params?: SchedulerListParams, options?: StreamQueryOptions) {
  return useQuery({
    queryKey: schedulerKeys.list(params),
    queryFn: () => schedulerApi.list(params),
    staleTime: 30 * 1000,
    enabled: !options?.streamEnabled,
  })
}

/**
 * Get scheduler statistics (admin only)
 */
export function useSchedulerStats(options?: StreamQueryOptions) {
  return useQuery({
    queryKey: schedulerKeys.stats(),
    queryFn: () => schedulerApi.getStats(),
    staleTime: 30 * 1000,
    enabled: !options?.streamEnabled,
  })
}

export function useSchedulerStreamUpdates(options: {
  enabled: boolean
  listParams?: SchedulerListParams
  intervalMs?: number
}) {
  const streamParams: SchedulerStreamParams = useMemo(
    () => ({
      category: options.listParams?.category,
      enabled_only: options.listParams?.enabled_only,
      interval_ms: options.intervalMs ?? 3000,
    }),
    [options.intervalMs, options.listParams?.category, options.listParams?.enabled_only],
  )

  return useSchedulerEventStream({
    enabled: options.enabled,
    streamParams,
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
 * Get per-run job event logs (admin only)
 */
export function useSchedulerJobLogs(jobId: string | undefined, limit: number = 20) {
  return useQuery({
    queryKey: schedulerKeys.logs(jobId!, limit),
    queryFn: () => schedulerApi.getLogs(jobId!, limit),
    enabled: !!jobId,
  })
}

/**
 * Get DMM hashlist ingestion status/checkpoints (admin only)
 */
export function useDmmHashlistStatus(enabled = true) {
  return useQuery({
    queryKey: schedulerKeys.dmmHashlistStatus(),
    queryFn: () => scrapersApi.getDMMHashlistStatus(),
    enabled,
    staleTime: 30 * 1000,
    refetchInterval: enabled ? 60 * 1000 : false,
  })
}

/**
 * Manually run a scheduler job (admin only)
 * Queues the job for background execution
 */
export function useRunSchedulerJob() {
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
  })
}

/**
 * Run a scheduler job inline/directly (admin only)
 * WARNING: This runs synchronously in FastAPI process - use for testing only
 */
export function useRunSchedulerJobInline() {
  return useMutation({
    mutationFn: (jobId: string) => schedulerApi.runInline(jobId),
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
      queryClient.invalidateQueries({ queryKey: schedulerKeys.dmmHashlistStatus() })
    },
  })
}

export function useImdbDatasetConfig(enabled = true) {
  return useQuery({
    queryKey: schedulerKeys.imdbDatasetConfig(),
    queryFn: () => scrapersApi.getImdbDatasetConfig(),
    enabled,
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
      if (!enabled) {
        return false
      }
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
    },
  })
}

export function useRunImdbDatasetImport() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: scrapersApi.runImdbDatasetImport,
    onSuccess: () => {
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
    onSuccess: (updatedJob) => {
      patchSchedulerJobInCache(queryClient, updatedJob)
    },
  })
}
