import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { userRssApi, type UserRSSFeedCreate, type UserRSSFeedUpdate } from '@/lib/api'

const RSS_FEEDS_QUERY_KEY = ['user-rss-feeds']

export function useRssFeeds() {
  return useQuery({
    queryKey: RSS_FEEDS_QUERY_KEY,
    queryFn: userRssApi.list,
  })
}

export function useRssFeed(feedId: string | undefined) {
  return useQuery({
    queryKey: [...RSS_FEEDS_QUERY_KEY, feedId],
    queryFn: () => userRssApi.get(feedId!),
    enabled: feedId !== undefined,
  })
}

export function useCreateRssFeed() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: UserRSSFeedCreate) => userRssApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
    },
  })
}

export function useUpdateRssFeed() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ feedId, data }: { feedId: string; data: UserRSSFeedUpdate }) => userRssApi.update(feedId, data),
    onSuccess: (_, { feedId }) => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: [...RSS_FEEDS_QUERY_KEY, feedId] })
    },
  })
}

export function useDeleteRssFeed() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (feedId: string) => userRssApi.delete(feedId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
    },
  })
}

export function useTestRssFeed() {
  return useMutation({
    mutationFn: (feedId: string) => userRssApi.testFeed(feedId),
  })
}

export function useTestRssFeedUrl() {
  return useMutation({
    mutationFn: ({ url, patterns }: { url: string; patterns?: Record<string, unknown> }) =>
      userRssApi.testUrl(url, patterns),
  })
}

export function useScrapeRssFeed() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (feedId: string) => userRssApi.scrapeFeed(feedId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
    },
  })
}

export function useRunRssScraper() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: () => userRssApi.runAll(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
    },
  })
}

export function useBulkUpdateRssFeedStatus() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ feedIds, isActive }: { feedIds: string[]; isActive: boolean }) =>
      userRssApi.bulkUpdateStatus(feedIds, isActive),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: RSS_FEEDS_QUERY_KEY })
    },
  })
}

export function useRssSchedulerStatus() {
  return useQuery({
    queryKey: ['rss-scheduler-status'],
    queryFn: userRssApi.getSchedulerStatus,
  })
}
