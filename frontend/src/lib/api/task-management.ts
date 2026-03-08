import { apiClient } from './client'

export interface TaskRecord {
  task_id: string
  actor_name: string | null
  queue_name: string | null
  status: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  updated_at: string | null
  duration_ms: number | null
  priority: number | null
  max_retries: number | null
  time_limit_ms: number | null
  worker_pid: number | null
  error_type: string | null
  error_message: string | null
  cancellation_requested: boolean
  cancellation_reason: string | null
  cancellation_requested_at: string | null
  args_preview: unknown[]
  kwargs_preview: Record<string, unknown>
  is_running_now: boolean
}

export interface TaskListResponse {
  tasks: TaskRecord[]
  total: number
  offset: number
  limit: number
  status_counts: Record<string, number>
  running_task_ids: string[]
}

export interface QueueSummary {
  queue_name: string
  stream_name: string
  recent_total: number
  status_counts: Record<string, number>
  currently_running: number
}

export interface TaskOverviewResponse {
  timestamp: string
  total_recent_tasks: number
  running_task_ids: string[]
  queue_summaries: QueueSummary[]
  global_status_counts: Record<string, number>
}

export interface CancelTaskRequest {
  reason?: string
}

export interface CancelTaskResponse {
  success: boolean
  task_id: string
  message: string
}

export interface RetryTaskResponse {
  success: boolean
  source_task_id: string
  new_task_id: string | null
  message: string
}

export interface BulkActionRequest {
  task_ids?: string[]
  status?: string
  queue_name?: string
  actor_name?: string
  search?: string
  limit?: number
  reason?: string
}

export interface BulkActionResponse {
  success: boolean
  message: string
  requested: number
  applied: number
  skipped: number
  task_ids: string[]
  new_task_ids: string[]
}

export interface TaskListParams {
  limit?: number
  offset?: number
  status?: string
  queue_name?: string
  actor_name?: string
  search?: string
}

export interface TaskStreamParams {
  sample_size?: number
  list_limit?: number
  list_offset?: number
  status?: string
  queue_name?: string
  actor_name?: string
  search?: string
  interval_ms?: number
}

export interface TaskStreamSnapshot {
  timestamp: string
  overview: TaskOverviewResponse
  list: TaskListResponse
}

function buildQuery(params: Record<string, string | number | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export const taskManagementApi = {
  getOverview: async (sampleSize: number = 500): Promise<TaskOverviewResponse> => {
    return apiClient.get<TaskOverviewResponse>(`/admin/tasks/overview?sample_size=${sampleSize}`)
  },

  listTasks: async (params: TaskListParams = {}): Promise<TaskListResponse> => {
    const query = buildQuery({
      limit: params.limit,
      offset: params.offset,
      status: params.status,
      queue_name: params.queue_name,
      actor_name: params.actor_name,
      search: params.search,
    })
    return apiClient.get<TaskListResponse>(`/admin/tasks${query}`)
  },

  getTask: async (taskId: string): Promise<TaskRecord> => {
    return apiClient.get<TaskRecord>(`/admin/tasks/${taskId}`)
  },

  cancelTask: async (taskId: string, payload: CancelTaskRequest = {}): Promise<CancelTaskResponse> => {
    return apiClient.post<CancelTaskResponse>(`/admin/tasks/${taskId}/cancel`, payload)
  },

  retryTask: async (taskId: string): Promise<RetryTaskResponse> => {
    return apiClient.post<RetryTaskResponse>(`/admin/tasks/${taskId}/retry`)
  },

  bulkCancelTasks: async (payload: BulkActionRequest): Promise<BulkActionResponse> => {
    return apiClient.post<BulkActionResponse>('/admin/tasks/bulk-cancel', payload)
  },

  bulkRetryTasks: async (payload: BulkActionRequest): Promise<BulkActionResponse> => {
    return apiClient.post<BulkActionResponse>('/admin/tasks/bulk-retry', payload)
  },

  connectTaskStream: async (
    params: TaskStreamParams,
    onSnapshot: (snapshot: TaskStreamSnapshot) => void,
    onError: (error: Error) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    const query = buildQuery({
      sample_size: params.sample_size,
      list_limit: params.list_limit,
      list_offset: params.list_offset,
      status: params.status,
      queue_name: params.queue_name,
      actor_name: params.actor_name,
      search: params.search,
      interval_ms: params.interval_ms,
    })

    const headers: HeadersInit = {}
    const token = apiClient.getAccessToken()
    const apiKey = apiClient.getApiKey()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    if (apiKey) {
      headers['X-API-Key'] = apiKey
    }

    const response = await fetch(`/api/v1/admin/tasks/stream${query}`, {
      method: 'GET',
      headers,
      signal,
    })

    if (!response.ok) {
      throw new Error(`Task stream failed: HTTP ${response.status}`)
    }
    if (!response.body) {
      throw new Error('Task stream failed: empty response body')
    }

    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          break
        }
        buffer += decoder.decode(value, { stream: true })

        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''
        for (const chunk of chunks) {
          const dataLines = chunk
            .split('\n')
            .filter((line) => line.startsWith('data:'))
            .map((line) => line.slice(5).trim())

          if (dataLines.length === 0) {
            continue
          }

          const data = dataLines.join('\n')
          try {
            const parsed = JSON.parse(data) as TaskStreamSnapshot
            onSnapshot(parsed)
          } catch (error) {
            onError(error instanceof Error ? error : new Error('Failed to parse task stream payload'))
          }
        }
      }
    } catch (error) {
      if (signal?.aborted) {
        return
      }
      onError(error instanceof Error ? error : new Error('Task stream connection closed'))
    } finally {
      reader.releaseLock()
    }
  },
}
