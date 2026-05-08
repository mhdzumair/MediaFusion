import { apiClient } from './client'

// ============================================
// Types
// ============================================

export interface RSSFeedParsingPatterns {
  title?: string
  description?: string
  pubDate?: string
  poster?: string
  background?: string
  logo?: string
  category?: string
  magnet?: string
  magnet_regex?: string
  torrent?: string
  torrent_regex?: string
  size?: string
  size_regex?: string
  seeders?: string
  seeders_regex?: string
  category_regex?: string
  episode_name_parser?: string
  // Regex group numbers
  magnet_regex_group?: number
  torrent_regex_group?: number
  size_regex_group?: number
  seeders_regex_group?: number
  category_regex_group?: number
}

export interface RSSFeedFilters {
  title_filter?: string
  title_exclude_filter?: string
  min_size_mb?: number
  max_size_mb?: number
  min_seeders?: number
  category_filter?: string[]
}

export interface RSSFeedMetrics {
  total_items_found: number
  total_items_processed: number
  total_items_skipped: number
  total_errors: number
  last_scrape_duration?: number
  items_processed_last_run: number
  items_skipped_last_run: number
  errors_last_run: number
  skip_reasons: Record<string, number>
}

export interface CatalogPattern {
  id?: string
  name?: string
  regex: string
  enabled: boolean
  case_sensitive: boolean
  target_catalogs: string[]
}

export interface RSSFeedOwner {
  id: string
  email: string
  username?: string
}

// User RSS Feed (new model with user ownership)
export interface UserRSSFeed {
  id: string
  user_id: string
  name: string
  url: string
  is_active: boolean
  source?: string
  torrent_type: string
  auto_detect_catalog: boolean
  parsing_patterns?: RSSFeedParsingPatterns
  filters?: RSSFeedFilters
  metrics?: RSSFeedMetrics
  catalog_patterns?: CatalogPattern[]
  last_scraped_at?: string
  created_at?: string
  updated_at?: string
  // Present for admin view
  user?: RSSFeedOwner
}

export interface UserRSSFeedCreate {
  name: string
  url: string
  is_active?: boolean
  source?: string
  torrent_type?: string
  auto_detect_catalog?: boolean
  parsing_patterns?: RSSFeedParsingPatterns
  filters?: RSSFeedFilters
  catalog_patterns?: CatalogPattern[]
}

export interface UserRSSFeedUpdate {
  name?: string
  url?: string
  is_active?: boolean
  source?: string
  torrent_type?: string
  auto_detect_catalog?: boolean
  parsing_patterns?: RSSFeedParsingPatterns
  filters?: RSSFeedFilters
  catalog_patterns?: CatalogPattern[]
}

export interface TestFeedResult {
  status: 'success' | 'error'
  message: string
  sample_item?: Record<string, unknown>
  detected_patterns?: Record<string, unknown>
  items_count?: number
  regex_results?: Record<string, unknown>
}

export interface RSSSchedulerStatus {
  crontab: string
  next_run?: string
  enabled: boolean
  last_global_run?: string
}

export interface ScrapeResult {
  status: string
  message: string
  items_processed: number
}

export interface BulkStatusResult {
  status: string
  message: string
  updated_count: number
}

// ============================================
// Legacy types (for backward compatibility)
// ============================================

export interface RSSFeed {
  id: number
  name: string
  url: string
  active: boolean
  auto_detect_catalog: boolean
  source?: string
  torrent_type?: string
  parsing_patterns?: Record<string, unknown>
  filters?: Record<string, unknown>
  catalog_patterns?: Array<Record<string, unknown>>
  last_scraped_at?: string
  created_at?: string
  updated_at?: string
}

export interface RSSFeedCreate {
  name: string
  url: string
  active?: boolean
  auto_detect_catalog?: boolean
  source?: string
  torrent_type?: string
  parsing_patterns?: Record<string, unknown>
  filters?: Record<string, unknown>
  catalog_patterns?: Array<Record<string, unknown>>
}

export interface RSSFeedUpdate {
  name?: string
  url?: string
  active?: boolean
  auto_detect_catalog?: boolean
  source?: string
  torrent_type?: string
  parsing_patterns?: Record<string, unknown>
  filters?: Record<string, unknown>
  catalog_patterns?: Array<Record<string, unknown>>
}

// ============================================
// API Client - New User RSS API (JWT Auth)
// ============================================

const USER_RSS_BASE = '/user-rss'

export const userRssApi = {
  /**
   * List RSS feeds (users see own, admins see all with user info)
   */
  list: async (): Promise<UserRSSFeed[]> => {
    return apiClient.get<UserRSSFeed[]>(`${USER_RSS_BASE}/feeds`)
  },

  /**
   * Get a specific RSS feed
   */
  get: async (feedId: string): Promise<UserRSSFeed> => {
    return apiClient.get<UserRSSFeed>(`${USER_RSS_BASE}/feeds/${feedId}`)
  },

  /**
   * Create a new RSS feed
   */
  create: async (data: UserRSSFeedCreate): Promise<UserRSSFeed> => {
    return apiClient.post<UserRSSFeed>(`${USER_RSS_BASE}/feeds`, data)
  },

  /**
   * Update an RSS feed
   */
  update: async (feedId: string, data: UserRSSFeedUpdate): Promise<UserRSSFeed> => {
    return apiClient.put<UserRSSFeed>(`${USER_RSS_BASE}/feeds/${feedId}`, data)
  },

  /**
   * Delete an RSS feed
   */
  delete: async (feedId: string): Promise<void> => {
    return apiClient.delete(`${USER_RSS_BASE}/feeds/${feedId}`)
  },

  /**
   * Test an existing RSS feed
   */
  testFeed: async (feedId: string): Promise<TestFeedResult> => {
    return apiClient.post<TestFeedResult>(`${USER_RSS_BASE}/feeds/${feedId}/test`)
  },

  /**
   * Test an RSS feed URL before creating
   */
  testUrl: async (url: string, patterns?: Record<string, unknown>): Promise<TestFeedResult> => {
    return apiClient.post<TestFeedResult>(`${USER_RSS_BASE}/feeds/test-url`, { url, patterns })
  },

  /**
   * Scrape a single feed
   */
  scrapeFeed: async (feedId: string): Promise<ScrapeResult> => {
    return apiClient.post<ScrapeResult>(`${USER_RSS_BASE}/feeds/${feedId}/scrape`)
  },

  /**
   * Run all scrapers (admin only)
   */
  runAll: async (): Promise<{ status: string; message: string }> => {
    return apiClient.post<{ status: string; message: string }>(`${USER_RSS_BASE}/feeds/run-all`)
  },

  /**
   * Bulk update feed status
   */
  bulkUpdateStatus: async (feedIds: string[], isActive: boolean): Promise<BulkStatusResult> => {
    return apiClient.post<BulkStatusResult>(`${USER_RSS_BASE}/feeds/bulk-status`, {
      feed_ids: feedIds,
      is_active: isActive,
    })
  },

  /**
   * Get scheduler status
   */
  getSchedulerStatus: async (): Promise<RSSSchedulerStatus> => {
    return apiClient.get<RSSSchedulerStatus>(`${USER_RSS_BASE}/scheduler-status`)
  },
}

// ============================================
// Legacy API Client (for backward compatibility)
// ============================================

export const rssApi = {
  /**
   * Get all RSS feeds
   * @deprecated Use userRssApi.list() instead
   */
  list: async (): Promise<RSSFeed[]> => {
    return apiClient.get<RSSFeed[]>('/rss/feeds')
  },

  /**
   * Get a specific RSS feed
   * @deprecated Use userRssApi.get() instead
   */
  get: async (feedId: number): Promise<RSSFeed> => {
    return apiClient.get<RSSFeed>(`/rss/feeds/${feedId}`)
  },

  /**
   * Create a new RSS feed
   * @deprecated Use userRssApi.create() instead
   */
  create: async (data: RSSFeedCreate): Promise<RSSFeed> => {
    return apiClient.post<RSSFeed>('/rss/feeds', data)
  },

  /**
   * Update an RSS feed
   * @deprecated Use userRssApi.update() instead
   */
  update: async (feedId: number, data: RSSFeedUpdate): Promise<RSSFeed> => {
    return apiClient.put<RSSFeed>(`/rss/feeds/${feedId}`, data)
  },

  /**
   * Delete an RSS feed
   * @deprecated Use userRssApi.delete() instead
   */
  delete: async (feedId: number): Promise<{ detail: string }> => {
    return apiClient.delete<{ detail: string }>(`/rss/feeds/${feedId}`)
  },

  /**
   * Test an RSS feed URL
   * @deprecated Use userRssApi.testUrl() instead
   */
  testFeed: async (url: string, patterns?: Record<string, unknown>): Promise<TestFeedResult> => {
    return apiClient.post<TestFeedResult>('/rss/feeds/test-feed', { url, patterns })
  },

  /**
   * Run the RSS feed scraper manually
   * @deprecated Use userRssApi.runAll() instead
   */
  runScraper: async (): Promise<{ detail: string }> => {
    return apiClient.post<{ detail: string }>('/rss/feeds/run')
  },

  /**
   * Activate or deactivate multiple feeds
   * @deprecated Use userRssApi.bulkUpdateStatus() instead
   */
  bulkUpdateStatus: async (feedIds: number[], activate: boolean): Promise<{ detail: string }> => {
    return apiClient.post<{ detail: string }>('/rss/feeds/activate-deactivate-feeds', {
      feed_ids: feedIds,
      activate,
    })
  },
}
