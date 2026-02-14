import { apiClient } from './client'

// Types
export type SchedulerCategory = 'scraper' | 'feed' | 'maintenance' | 'background'

export interface SchedulerJobInfo {
  id: string
  display_name: string
  category: SchedulerCategory
  description: string
  crontab: string
  is_enabled: boolean
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
  run_at: string
  duration_seconds: number | null
  status: string
  items_scraped: number | null
  error: string | null
}

export interface JobHistoryResponse {
  job_id: string
  display_name: string
  entries: JobHistoryEntry[]
  total: number
}

export interface SchedulerListParams {
  category?: SchedulerCategory
  enabled_only?: boolean
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

  /**
   * Manually run a scheduler job (admin only)
   * This queues the job for execution in the background worker
   */
  run: async (jobId: string): Promise<ManualRunResponse> => {
    return apiClient.post<ManualRunResponse>(`/admin/schedulers/${jobId}/run`)
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
}

