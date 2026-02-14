import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import { 
  watchHistoryApi, 
  type WatchHistoryListParams, 
  type WatchHistoryCreateRequest,
  type WatchHistoryUpdateRequest,
  type StreamActionTrackRequest,
} from '@/lib/api'

const WATCH_HISTORY_QUERY_KEY = ['watch-history']
const CONTINUE_WATCHING_QUERY_KEY = ['continue-watching']

export function useWatchHistory(params: WatchHistoryListParams = {}) {
  return useQuery({
    queryKey: [...WATCH_HISTORY_QUERY_KEY, params],
    queryFn: () => watchHistoryApi.list(params),
  })
}

export function useInfiniteWatchHistory(params: Omit<WatchHistoryListParams, 'page'> = {}) {
  return useInfiniteQuery({
    queryKey: [...WATCH_HISTORY_QUERY_KEY, 'infinite', params],
    queryFn: ({ pageParam = 1 }) => watchHistoryApi.list({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.page + 1 : undefined,
    initialPageParam: 1,
  })
}

export function useContinueWatching(profileId?: number, limit: number = 10) {
  return useQuery({
    queryKey: [...CONTINUE_WATCHING_QUERY_KEY, profileId, limit],
    queryFn: () => watchHistoryApi.getContinueWatching(profileId, limit),
  })
}

export function useCreateWatchHistory() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (data: WatchHistoryCreateRequest) => watchHistoryApi.create(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WATCH_HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: CONTINUE_WATCHING_QUERY_KEY })
    },
  })
}

export function useUpdateWatchProgress() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: ({ historyId, data }: { historyId: number; data: WatchHistoryUpdateRequest }) =>
      watchHistoryApi.updateProgress(historyId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WATCH_HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: CONTINUE_WATCHING_QUERY_KEY })
    },
  })
}

export function useDeleteWatchHistory() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (historyId: number) => watchHistoryApi.delete(historyId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WATCH_HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: CONTINUE_WATCHING_QUERY_KEY })
    },
  })
}

export function useClearWatchHistory() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (profileId?: number) => watchHistoryApi.clear(profileId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WATCH_HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: CONTINUE_WATCHING_QUERY_KEY })
    },
  })
}

export function useTrackStreamAction() {
  const queryClient = useQueryClient()
  
  return useMutation({
    mutationFn: (data: StreamActionTrackRequest) => watchHistoryApi.trackAction(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: WATCH_HISTORY_QUERY_KEY })
      queryClient.invalidateQueries({ queryKey: CONTINUE_WATCHING_QUERY_KEY })
    },
  })
}

