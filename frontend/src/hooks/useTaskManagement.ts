import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { taskManagementApi, type TaskListParams, type TaskStreamParams } from '@/lib/api'

export const taskManagementKeys = {
  all: ['task-management'] as const,
  overview: (sampleSize: number) => [...taskManagementKeys.all, 'overview', sampleSize] as const,
  list: (params: TaskListParams) => [...taskManagementKeys.all, 'list', params] as const,
  detail: (taskId: string) => [...taskManagementKeys.all, 'detail', taskId] as const,
}

export function useTaskOverview(sampleSize: number = 500) {
  return useQuery({
    queryKey: taskManagementKeys.overview(sampleSize),
    queryFn: () => taskManagementApi.getOverview(sampleSize),
    staleTime: 15 * 1000,
    refetchInterval: 30 * 1000,
  })
}

export function useTaskList(params: TaskListParams) {
  return useQuery({
    queryKey: taskManagementKeys.list(params),
    queryFn: () => taskManagementApi.listTasks(params),
    staleTime: 10 * 1000,
    refetchInterval: 20 * 1000,
  })
}

export function useTaskDetail(taskId: string | null) {
  return useQuery({
    queryKey: taskManagementKeys.detail(taskId || ''),
    queryFn: () => taskManagementApi.getTask(taskId || ''),
    enabled: !!taskId,
    staleTime: 10 * 1000,
    refetchInterval: 20 * 1000,
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
  const [isConnected, setIsConnected] = useState(false)
  const [lastEventAt, setLastEventAt] = useState<string | null>(null)

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
    [options.intervalMs, options.listParams, options.sampleSize],
  )

  useEffect(() => {
    if (!options.enabled) {
      setIsConnected(false)
      return
    }

    const controller = new AbortController()
    let reconnectTimer: number | undefined

    const connect = async () => {
      try {
        setIsConnected(false)
        await taskManagementApi.connectTaskStream(
          streamParams,
          (snapshot) => {
            setIsConnected(true)
            setLastEventAt(snapshot.timestamp)
            queryClient.setQueryData(taskManagementKeys.overview(options.sampleSize), snapshot.overview)
            queryClient.setQueryData(taskManagementKeys.list(options.listParams), snapshot.list)
          },
          () => {
            setIsConnected(false)
          },
          controller.signal,
        )
      } catch {
        if (controller.signal.aborted) {
          return
        }
      } finally {
        if (!controller.signal.aborted) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    connect()

    return () => {
      controller.abort()
      setIsConnected(false)
      if (reconnectTimer !== undefined) {
        window.clearTimeout(reconnectTimer)
      }
    }
  }, [options.enabled, options.listParams, options.sampleSize, queryClient, streamParams])

  return {
    isConnected,
    lastEventAt,
  }
}
