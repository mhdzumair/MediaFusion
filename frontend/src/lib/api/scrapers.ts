import { apiClient } from './client'

export interface ScraperTask {
  spider_name: string
  pages?: number
  start_page?: number
  search_keyword?: string
  scrape_all?: boolean
  scrap_catalog_id?: string
  total_pages?: number
}

export interface ScraperResponse {
  status: 'success' | 'error'
  message: string
}

export interface BlockTorrentRequest {
  info_hash: string
  reason?: string
}

// Content scraping types
export interface ScraperInfo {
  id: string
  name: string
  enabled: boolean
  requires_debrid: boolean
  ttl: number
  description: string
}

export interface ScraperStatusInfo {
  last_scraped: string | null
  cooldown_remaining: number
  can_scrape: boolean
  ttl: number
  enabled: boolean
  requires_debrid: boolean
}

export interface ScrapeStatusResponse {
  media_id: number
  title: string | null
  last_scraped_at: string | null
  cooldown_remaining: number | null
  can_scrape: boolean
  scraper_statuses: Record<string, ScraperStatusInfo> | null
  available_scrapers: ScraperInfo[]
  is_moderator: boolean
  has_debrid: boolean // Whether user has debrid service configured
}

export interface ScrapeRequest {
  media_type: 'movie' | 'series'
  season?: number
  episode?: number
  force?: boolean
  scrapers?: string[] // List of scraper IDs to use
}

export interface ScrapeResponse {
  status: string
  message: string
  media_id: number
  title: string | null
  streams_found: number
  scraped_at: string | null
  scrapers_used: string[]
  scrapers_skipped: string[]
}

export const scrapersApi = {
  /**
   * Run a scraper task
   */
  runScraper: async (task: ScraperTask): Promise<ScraperResponse> => {
    return apiClient.post<ScraperResponse>('/admin/scrapers/run', task)
  },

  /**
   * Block a torrent by info_hash
   */
  blockTorrent: async (data: BlockTorrentRequest): Promise<ScraperResponse> => {
    return apiClient.post<ScraperResponse>('/admin/scrapers/block-torrent', data)
  },

  /**
   * Unblock a torrent by info_hash
   */
  unblockTorrent: async (infoHash: string): Promise<ScraperResponse> => {
    return apiClient.post<ScraperResponse>(`/admin/scrapers/unblock-torrent?info_hash=${infoHash}`)
  },

  /**
   * List available spiders
   */
  listSpiders: async (): Promise<{ spiders: Array<{ id: string; name: string }> }> => {
    return apiClient.get('/admin/scrapers/spiders')
  },

  /**
   * Get catalog data for scrapers
   */
  getCatalogData: async (): Promise<{
    catalog_data: Record<string, unknown>
    supported_series_catalogs: string[]
    supported_movie_catalogs: string[]
    supported_languages: string[]
  }> => {
    return apiClient.get('/admin/scrapers/catalogs')
  },

  /**
   * Get status of all scrapers
   */
  getScraperStatus: async (): Promise<{
    scrapers: Array<{
      spider_id: string
      spider_name: string
      last_run: string | null
      time_since_last_run: string | null
    }>
  }> => {
    return apiClient.get('/admin/scrapers/status')
  },

  // ============================================
  // Content Scraping API
  // ============================================

  /**
   * Get scrape status for a specific media item
   */
  getScrapeStatus: async (
    mediaId: number,
    mediaType: 'movie' | 'series' = 'movie',
    season?: number,
    episode?: number,
  ): Promise<ScrapeStatusResponse> => {
    const params = new URLSearchParams({ media_type: mediaType })
    if (season !== undefined) params.append('season', String(season))
    if (episode !== undefined) params.append('episode', String(episode))
    return apiClient.get(`/scraping/${mediaId}/status?${params}`)
  },

  /**
   * Trigger scraping for a media item
   */
  triggerScrape: async (mediaId: number, request: ScrapeRequest): Promise<ScrapeResponse> => {
    return apiClient.post(`/scraping/${mediaId}/scrape`, request)
  },
}
