import { useQuery } from '@tanstack/react-query'
import { metricsApi } from '@/lib/api'

const METRICS_QUERY_KEY = ['metrics']

export function useTorrentCount() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'torrent-count'],
    queryFn: metricsApi.getTorrentCount,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useTorrentSources() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'torrent-sources'],
    queryFn: metricsApi.getTorrentSources,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useMetadataCount() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'metadata-count'],
    queryFn: metricsApi.getMetadataCount,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useScrapySchedulers() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'scrapy-schedulers'],
    queryFn: metricsApi.getScrapySchedulers,
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // Refetch every minute
  })
}

export function useRedisMetrics() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'redis'],
    queryFn: metricsApi.getRedisMetrics,
    staleTime: 30 * 1000, // 30 seconds
  })
}

export function useDebridCacheMetrics() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'debrid-cache'],
    queryFn: metricsApi.getDebridCacheMetrics,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useTorrentUploaders() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'uploaders'],
    queryFn: metricsApi.getTorrentUploaders,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })
}

export function useWeeklyUploaders(weekDate: string) {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'weekly-uploaders', weekDate],
    queryFn: () => metricsApi.getWeeklyUploaders(weekDate),
    staleTime: 10 * 60 * 1000, // 10 minutes
    enabled: !!weekDate,
  })
}

export function useUserStats() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'user-stats'],
    queryFn: metricsApi.getUserStats,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useContributionMetrics() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'contribution-stats'],
    queryFn: metricsApi.getContributionStats,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useActivityStats() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'activity-stats'],
    queryFn: metricsApi.getActivityStats,
    staleTime: 60 * 1000, // 1 minute
  })
}

export function useSystemOverview() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'system-overview'],
    queryFn: metricsApi.getSystemOverview,
    staleTime: 30 * 1000, // 30 seconds
  })
}

// Scraper Metrics Hooks
export function useScraperMetrics() {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'scraper-metrics'],
    queryFn: metricsApi.getScraperMetrics,
    staleTime: 30 * 1000, // 30 seconds
    refetchInterval: 60 * 1000, // Refetch every minute
  })
}

export function useScraperAggregatedStats(scraperName: string | null) {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'scraper-aggregated', scraperName],
    queryFn: () => metricsApi.getScraperAggregatedStats(scraperName!),
    staleTime: 30 * 1000,
    enabled: !!scraperName,
  })
}

export function useScraperHistory(scraperName: string | null, limit: number = 20) {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'scraper-history', scraperName, limit],
    queryFn: () => metricsApi.getScraperHistory(scraperName!, limit),
    staleTime: 30 * 1000,
    enabled: !!scraperName,
  })
}

export function useScraperLatestMetrics(scraperName: string | null) {
  return useQuery({
    queryKey: [...METRICS_QUERY_KEY, 'scraper-latest', scraperName],
    queryFn: () => metricsApi.getScraperLatestMetrics(scraperName!),
    staleTime: 30 * 1000,
    enabled: !!scraperName,
  })
}

// Combined metrics hook for dashboard
export function useDashboardMetrics() {
  const torrentCount = useTorrentCount()
  const metadataCount = useMetadataCount()
  const torrentSources = useTorrentSources()
  const scrapySchedulers = useScrapySchedulers()

  return {
    torrentCount,
    metadataCount,
    torrentSources,
    scrapySchedulers,
    isLoading: 
      torrentCount.isLoading || 
      metadataCount.isLoading || 
      torrentSources.isLoading || 
      scrapySchedulers.isLoading,
    isError:
      torrentCount.isError ||
      metadataCount.isError ||
      torrentSources.isError ||
      scrapySchedulers.isError,
  }
}

