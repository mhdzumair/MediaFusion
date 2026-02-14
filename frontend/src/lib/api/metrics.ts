import { apiClient } from './client'

export interface TorrentCount {
  total_torrents: number
  total_torrents_readable: string
}

export interface TorrentSource {
  name: string
  count: number
}

export interface MetadataCount {
  movies: number
  series: number
  tv_channels: number
}

export interface SpiderLastRun {
  name: string
  spider_id: string
  last_run: string | null
  time_since_last_run: string | null
  time_since_last_run_seconds: number
}

// Full scheduler job info from /api/v1/admin/schedulers
export interface SchedulerJobLastRunState {
  item_scraped_count?: number
  item_dropped_count?: number
  log_count_info?: number
  log_count_warning?: number
  log_count_error?: number
  [key: string]: unknown
}

export interface SchedulerJobInfo {
  id: string
  display_name: string
  category: string
  description: string
  crontab: string
  is_enabled: boolean
  last_run: string | null
  last_run_timestamp: number | null
  time_since_last_run: string
  next_run_in: string | null
  next_run_timestamp: number | null
  last_run_state: SchedulerJobLastRunState | null
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

// Redis metrics - matches the actual API response structure
export interface RedisPoolStats {
  in_use: number
  available: number
  max: number
}

export interface RedisMetrics {
  timestamp: string
  app_pool_stats?: {
    app_connections: {
      async: RedisPoolStats
      sync?: RedisPoolStats
    }
  }
  memory?: {
    used_memory_human: string | null
    used_memory_peak_human: string | null
    maxmemory_human: string | null
    mem_fragmentation_ratio: number | null
  }
  connections?: {
    connected_clients: number | null
    blocked_clients: number | null
    maxclients: number | null
  }
  performance?: {
    instantaneous_ops_per_sec: number | null
    total_commands_processed: number | null
  }
  cache?: {
    keyspace_hits: number | null
    keyspace_misses: number | null
    hit_rate: number
  }
  error?: string
}

// Debrid cache metrics - matches the actual API response structure
export interface DebridCacheMetrics {
  timestamp: string
  services: Record<
    string,
    {
      cached_torrents: number
    }
  >
  error?: string
}

export interface TorrentUploader {
  name: string
  count: number
  user_id?: number | null
  is_linked?: boolean
  latest_upload?: string
}

export interface WeeklyUploadersResponse {
  week_start: string
  week_end: string
  uploaders: TorrentUploader[]
  error?: string
}

// User Statistics
export interface UserStats {
  timestamp: string
  total_users: number
  active_users: {
    daily: number
    weekly: number
    monthly: number
  }
  new_users_this_week: number
  verified_users: number
  unverified_users: number
  users_by_role: Record<string, number>
  users_by_contribution_level: Record<string, number>
  total_profiles: number
  avg_profiles_per_user: number
  error?: string
}

// Contribution Statistics
export interface ContributionStats {
  timestamp: string
  total_contributions: number
  contributions_by_status: Record<string, number>
  pending_review: number
  recent_contributions_week: number
  total_stream_votes: number
  total_metadata_votes: number
  unique_contributors: number
  error?: string
}

// Activity Statistics
export interface ActivityStats {
  timestamp: string
  watch_history: {
    total_entries: number
    recent_week: number
    unique_users: number
  }
  downloads: {
    total: number
  }
  library: {
    total_items: number
  }
  playback: {
    total_entries: number
    total_plays: number
  }
  rss_feeds: {
    total: number
    active: number
  }
  error?: string
}

// System Overview
export interface SystemOverview {
  timestamp: string
  torrents: {
    total: number
    formatted: string
  }
  content: {
    total: number
    movies: number
    series: number
    tv_channels: number
  }
  users: {
    total: number
    active_today: number
  }
  moderation: {
    pending_contributions: number
  }
  error?: string
}

// Scraper Metrics Types
export interface ScraperMetricsTotalItems {
  found: number
  processed: number
  skipped: number
  errors: number
}

export interface ScraperMetricsIndexerStats {
  success_count: number
  error_count: number
  results_count: number
  errors: Record<string, number>
}

export interface ScraperMetricsSummary {
  scraper_name: string
  timestamp: string
  end_timestamp: string
  duration_seconds: number
  meta_id?: string | null
  meta_title?: string | null
  season?: number | null
  episode?: number | null
  skip_scraping: boolean
  total_items: ScraperMetricsTotalItems
  error_counts: Record<string, number>
  skip_reasons: Record<string, number>
  quality_distribution: Record<string, number>
  source_distribution: Record<string, number>
  indexer_stats?: Record<string, ScraperMetricsIndexerStats> | null
}

export interface ScraperAggregatedStats {
  scraper_name: string
  total_runs: number
  total_items_found: number
  total_items_processed: number
  total_items_skipped: number
  total_errors: number
  total_duration_seconds: number
  successful_runs: number
  failed_runs: number
  skipped_runs: number
  error_distribution: Record<string, number>
  skip_reason_distribution: Record<string, number>
  quality_distribution: Record<string, number>
  source_distribution: Record<string, number>
  last_run?: string | null
  last_successful_run?: string | null
  success_rate?: number | null
  avg_duration_seconds?: number | null
  avg_items_per_run?: number | null
}

export interface ScraperMetricsData {
  scraper_name: string
  latest: ScraperMetricsSummary | null
  aggregated: ScraperAggregatedStats | null
}

export interface ScraperMetricsResponse {
  timestamp: string
  scrapers: ScraperMetricsData[]
  total_scrapers: number
}

export interface ScraperHistoryResponse {
  scraper_name: string
  history: ScraperMetricsSummary[]
  total: number
}

export const metricsApi = {
  /**
   * Get total torrent count
   * Requires admin role
   */
  getTorrentCount: async (): Promise<TorrentCount> => {
    return apiClient.get<TorrentCount>('/admin/metrics/torrents')
  },

  /**
   * Get torrents by source
   * Requires admin role
   */
  getTorrentSources: async (): Promise<TorrentSource[]> => {
    return apiClient.get<TorrentSource[]>('/admin/metrics/torrents/sources')
  },

  /**
   * Get metadata counts
   * Requires admin role
   */
  getMetadataCount: async (): Promise<MetadataCount> => {
    return apiClient.get<MetadataCount>('/admin/metrics/metadata')
  },

  /**
   * Get scrapy schedulers last run times (basic info)
   * Requires admin role
   */
  getScrapySchedulers: async (): Promise<SpiderLastRun[]> => {
    return apiClient.get<SpiderLastRun[]>('/admin/metrics/scrapy-schedulers')
  },

  /**
   * Get full scheduler jobs info with detailed state
   * Requires admin role
   */
  getSchedulerJobs: async (category?: string): Promise<SchedulerJobsResponse> => {
    const params = category ? `?category=${category}` : ''
    return apiClient.get<SchedulerJobsResponse>(`/admin/schedulers${params}`)
  },

  /**
   * Get Redis metrics
   * Requires admin role
   */
  getRedisMetrics: async (): Promise<RedisMetrics> => {
    return apiClient.get<RedisMetrics>('/admin/metrics/redis')
  },

  /**
   * Get debrid cache metrics
   * Requires admin role
   */
  getDebridCacheMetrics: async (): Promise<DebridCacheMetrics> => {
    return apiClient.get<DebridCacheMetrics>('/admin/metrics/debrid-cache')
  },

  /**
   * Get torrents by uploaders
   * Requires admin role
   */
  getTorrentUploaders: async (): Promise<TorrentUploader[]> => {
    return apiClient.get<TorrentUploader[]>('/admin/metrics/torrents/uploaders')
  },

  /**
   * Get weekly top uploaders
   * Requires admin role
   */
  getWeeklyUploaders: async (weekDate: string): Promise<WeeklyUploadersResponse> => {
    return apiClient.get<WeeklyUploadersResponse>(`/admin/metrics/torrents/uploaders/weekly/${weekDate}`)
  },

  /**
   * Get user statistics
   * Requires admin role
   */
  getUserStats: async (): Promise<UserStats> => {
    return apiClient.get<UserStats>('/admin/metrics/users/stats')
  },

  /**
   * Get contribution statistics
   * Requires admin role
   */
  getContributionStats: async (): Promise<ContributionStats> => {
    return apiClient.get<ContributionStats>('/admin/metrics/contributions/stats')
  },

  /**
   * Get activity statistics
   * Requires admin role
   */
  getActivityStats: async (): Promise<ActivityStats> => {
    return apiClient.get<ActivityStats>('/admin/metrics/activity/stats')
  },

  /**
   * Get system overview
   * Requires admin role
   */
  getSystemOverview: async (): Promise<SystemOverview> => {
    return apiClient.get<SystemOverview>('/admin/metrics/system/overview')
  },

  /**
   * Get all scraper metrics overview
   * Requires admin role
   */
  getScraperMetrics: async (): Promise<ScraperMetricsResponse> => {
    return apiClient.get<ScraperMetricsResponse>('/admin/metrics/scrapers')
  },

  /**
   * Get aggregated stats for a specific scraper
   * Requires admin role
   */
  getScraperAggregatedStats: async (scraperName: string): Promise<ScraperAggregatedStats> => {
    return apiClient.get<ScraperAggregatedStats>(`/admin/metrics/scrapers/${encodeURIComponent(scraperName)}`)
  },

  /**
   * Get run history for a specific scraper
   * Requires admin role
   */
  getScraperHistory: async (scraperName: string, limit: number = 20): Promise<ScraperHistoryResponse> => {
    return apiClient.get<ScraperHistoryResponse>(
      `/admin/metrics/scrapers/${encodeURIComponent(scraperName)}/history?limit=${limit}`,
    )
  },

  /**
   * Get latest metrics for a specific scraper
   * Requires admin role
   */
  getScraperLatestMetrics: async (scraperName: string): Promise<ScraperMetricsSummary> => {
    return apiClient.get<ScraperMetricsSummary>(`/admin/metrics/scrapers/${encodeURIComponent(scraperName)}/latest`)
  },

  /**
   * Clear metrics for a specific scraper
   * Requires admin role
   */
  clearScraperMetrics: async (scraperName: string): Promise<{ message: string; keys_deleted: number }> => {
    return apiClient.delete<{ message: string; keys_deleted: number }>(
      `/admin/metrics/scrapers/${encodeURIComponent(scraperName)}/metrics`,
    )
  },
}
