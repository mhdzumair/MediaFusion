import { apiClient } from './client'
import { connectEventStream } from './sse'

// Types
export type SchedulerCategory = 'scraper' | 'feed' | 'maintenance' | 'background'

export interface SchedulerJobInfo {
  id: string
  display_name: string
  category: SchedulerCategory
  description: string
  crontab: string
  is_enabled: boolean
  cron_configured?: boolean
  payload?: Record<string, unknown>
  last_run: string | null
  last_run_timestamp: number | null
  time_since_last_run: string
  next_run_in: string | null
  next_run_timestamp: number | null
  last_run_state: Record<string, unknown> | null
  is_running: boolean
}

export interface SchedulerJobsResponse {
  jobs: SchedulerJobInfo[]
  total: number
  active: number
  disabled: number
  running: number
  global_scheduler_disabled: boolean
}

export interface SchedulerStatsResponse {
  total_jobs: number
  active_jobs: number
  disabled_jobs: number
  running_jobs: number
  jobs_by_category: Record<string, { total: number; active: number }>
  global_scheduler_disabled: boolean
}

export interface ManualRunResponse {
  success: boolean
  message: string
  job_id: string
}

export interface InlineRunResponse {
  success: boolean
  message: string
  job_id: string
  execution_time_seconds: number
  result: Record<string, unknown> | null
  error: string | null
}

export interface JobHistoryEntry {
  job_id: number
  status: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  attempts: number
}

export interface JobHistoryResponse {
  job_id: string
  display_name: string
  entries: JobHistoryEntry[]
  total: number
}

export interface SchedulerJobEvent {
  event: string
  detail: string | null
  at: string | null
}

export interface SchedulerJobLogRun {
  job_id: number
  status: string
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  error: string | null
  attempts: number
  events: SchedulerJobEvent[]
}

export interface SchedulerJobLogsResponse {
  job_id: string
  display_name: string
  runs: SchedulerJobLogRun[]
  total: number
}

export interface SchedulerStreamSnapshot {
  timestamp: string
  list: SchedulerJobsResponse
  stats: SchedulerStatsResponse
}

export interface SchedulerListParams {
  category?: SchedulerCategory
  enabled_only?: boolean
}

export interface SchedulerStreamParams {
  category?: SchedulerCategory
  enabled_only?: boolean
  interval_ms?: number
}

export interface UpdateSchedulerJobRequest {
  enabled?: boolean
  schedule?: string
  payload?: Record<string, unknown>
}

export function computeHistoryDurationSeconds(startedAt: string | null, finishedAt: string | null): number | null {
  if (!startedAt || !finishedAt) {
    return null
  }
  const started = Date.parse(startedAt)
  const finished = Date.parse(finishedAt)
  if (Number.isNaN(started) || Number.isNaN(finished)) {
    return null
  }
  const seconds = (finished - started) / 1000
  return seconds >= 0 ? seconds : null
}

// API functions
export const schedulerApi = {
  /**
   * List all scheduler jobs (admin only)
   */
  list: async (params: SchedulerListParams = {}): Promise<SchedulerJobsResponse> => {
    const searchParams = new URLSearchParams()
    if (params.category) searchParams.set('category', params.category)
    if (params.enabled_only) searchParams.set('enabled_only', 'true')

    const query = searchParams.toString()
    return apiClient.get<SchedulerJobsResponse>(`/admin/schedulers${query ? `?${query}` : ''}`)
  },

  /**
   * Get scheduler stats (admin only)
   */
  getStats: async (): Promise<SchedulerStatsResponse> => {
    return apiClient.get<SchedulerStatsResponse>('/admin/schedulers/stats')
  },

  /**
   * Get single scheduler job (admin only)
   */
  get: async (jobId: string): Promise<SchedulerJobInfo> => {
    return apiClient.get<SchedulerJobInfo>(`/admin/schedulers/${jobId}`)
  },

  update: async (jobId: string, payload: UpdateSchedulerJobRequest): Promise<SchedulerJobInfo> => {
    return apiClient.patch<SchedulerJobInfo>(`/admin/schedulers/${jobId}`, payload)
  },

  /**
   * Manually run a scheduler job (admin only)
   * This queues the job for execution in the background worker
   */
  run: async (
    jobId: string,
    options: { forceRun?: boolean; payload?: Record<string, unknown> } = {},
  ): Promise<ManualRunResponse> => {
    const searchParams = new URLSearchParams()
    if (options.forceRun) {
      searchParams.set('force_run', 'true')
    }
    const query = searchParams.toString()
    return apiClient.post<ManualRunResponse>(
      `/admin/schedulers/${jobId}/run${query ? `?${query}` : ''}`,
      options.payload ?? {},
    )
  },

  /**
   * Run a scheduler job inline/directly (admin only)
   * WARNING: This runs the job synchronously in the FastAPI process.
   * Use only for testing purposes as it may take a long time.
   */
  runInline: async (jobId: string): Promise<InlineRunResponse> => {
    return apiClient.post<InlineRunResponse>(`/admin/schedulers/${jobId}/run-inline`)
  },

  /**
   * Get job execution history (admin only)
   */
  getHistory: async (jobId: string, limit: number = 20): Promise<JobHistoryResponse> => {
    return apiClient.get<JobHistoryResponse>(`/admin/schedulers/${jobId}/history?limit=${limit}`)
  },

  /**
   * Get per-run job event logs (admin only)
   */
  getLogs: async (jobId: string, limit: number = 20): Promise<SchedulerJobLogsResponse> => {
    return apiClient.get<SchedulerJobLogsResponse>(`/admin/schedulers/${jobId}/logs?limit=${limit}`)
  },

  connectStream: async (
    params: SchedulerStreamParams,
    onSnapshot: (snapshot: SchedulerStreamSnapshot) => void,
    onError: (error: Error) => void,
    signal?: AbortSignal,
  ): Promise<void> => {
    return connectEventStream<SchedulerStreamSnapshot>(
      '/admin/schedulers/stream',
      {
        category: params.category,
        enabled_only: params.enabled_only ? 'true' : undefined,
        interval_ms: params.interval_ms,
      },
      'snapshot',
      onSnapshot,
      onError,
      signal,
    )
  },
}
