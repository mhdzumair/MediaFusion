import { useMemo } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { taskManagementApi, type TaskDetailRecord, type TaskListParams, type TaskStreamParams } from '@/lib/api'
import { useEventStreamConnection } from './useEventStreamConnection'

export const taskManagementKeys = {
  all: ['task-management'] as const,
  overview: (sampleSize: number) => [...taskManagementKeys.all, 'overview', sampleSize] as const,
  list: (params: TaskListParams) => [...taskManagementKeys.all, 'list', params] as const,
  detail: (taskId: string) => [...taskManagementKeys.all, 'detail', taskId] as const,
}

type StreamQueryOptions = {
  streamEnabled?: boolean
}

export function useTaskOverview(sampleSize: number = 500, options?: StreamQueryOptions) {
  return useQuery({
    queryKey: taskManagementKeys.overview(sampleSize),
    queryFn: () => taskManagementApi.getOverview(sampleSize),
    staleTime: 15 * 1000,
    enabled: !options?.streamEnabled,
  })
}

export function useTaskList(params: TaskListParams, options?: StreamQueryOptions) {
  return useQuery({
    queryKey: taskManagementKeys.list(params),
    queryFn: () => taskManagementApi.listTasks(params),
    staleTime: 10 * 1000,
    placeholderData: keepPreviousData,
    enabled: !options?.streamEnabled,
  })
}

export function useTaskDetail(taskId: string | null, options?: { enabled?: boolean; streamEnabled?: boolean }) {
  const streamActive = options?.streamEnabled ?? false
  return useQuery({
    queryKey: taskManagementKeys.detail(taskId || ''),
    queryFn: () => taskManagementApi.getTask(taskId || ''),
    enabled: (options?.enabled ?? !!taskId) && !streamActive,
    staleTime: 10 * 1000,
  })
}

export function useTaskDetailStream(taskId: string | null, enabled: boolean = true, intervalMs: number = 3000) {
  const queryClient = useQueryClient()
  const streamKey = useMemo(
    () => JSON.stringify({ taskId, intervalMs, enabled: !!enabled && !!taskId }),
    [enabled, intervalMs, taskId],
  )

  const connect = useMemo(
    () => async (signal: AbortSignal, onConnected: () => void) => {
      if (!taskId) {
        return
      }
      await taskManagementApi.connectTaskDetailStream(
        taskId,
        (detail) => {
          onConnected()
          queryClient.setQueryData(taskManagementKeys.detail(taskId), detail)
        },
        () => {},
        signal,
        intervalMs,
      )
    },
    [intervalMs, queryClient, taskId],
  )

  return useEventStreamConnection({
    enabled: enabled && !!taskId,
    streamKey,
    connect,
  })
}

export function useCancelTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ taskId, reason }: { taskId: string; reason?: string }) =>
      taskManagementApi.cancelTask(taskId, { reason }),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.all })
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.detail(variables.taskId) })
    },
  })
}

export function useRetryTask() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (taskId: string) => taskManagementApi.retryTask(taskId),
    onSuccess: (_, taskId) => {
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.all })
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.detail(taskId) })
    },
  })
}

export function useBulkCancelTasks() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: taskManagementApi.bulkCancelTasks,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.all })
    },
  })
}

export function useBulkRetryTasks() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: taskManagementApi.bulkRetryTasks,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: taskManagementKeys.all })
    },
  })
}

export function useTaskStreamUpdates(options: {
  enabled: boolean
  sampleSize: number
  listParams: TaskListParams
  intervalMs?: number
}) {
  const queryClient = useQueryClient()

  const streamParams: TaskStreamParams = useMemo(
    () => ({
      sample_size: options.sampleSize,
      list_limit: options.listParams.limit ?? 100,
      list_offset: options.listParams.offset ?? 0,
      status: options.listParams.status,
      queue_name: options.listParams.queue_name,
      actor_name: options.listParams.actor_name,
      search: options.listParams.search,
      interval_ms: options.intervalMs ?? 3000,
    }),
    [
      options.intervalMs,
      options.listParams.actor_name,
      options.listParams.limit,
      options.listParams.offset,
      options.listParams.queue_name,
      options.listParams.search,
      options.listParams.status,
      options.sampleSize,
    ],
  )

  const streamKey = useMemo(() => JSON.stringify(streamParams), [streamParams])

  const connect = useMemo(
    () => async (signal: AbortSignal, onConnected: () => void) => {
      await taskManagementApi.connectTaskStream(
        streamParams,
        (snapshot) => {
          onConnected()
          queryClient.setQueryData(taskManagementKeys.overview(options.sampleSize), snapshot.overview)
          queryClient.setQueryData(taskManagementKeys.list(options.listParams), snapshot.list)
        },
        () => {},
        signal,
      )
    },
    [options.listParams, options.sampleSize, queryClient, streamParams],
  )

  return useEventStreamConnection({
    enabled: options.enabled,
    streamKey,
    connect,
  })
}

export type { TaskDetailRecord }
