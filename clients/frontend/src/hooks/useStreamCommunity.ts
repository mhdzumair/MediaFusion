import { useQuery, useQueryClient } from '@tanstack/react-query'
import { streamCommunityApi } from '@/lib/api/stream-community'

export const streamCommunityKeys = {
  all: ['stream-community'] as const,
  bulk: (streamIds: number[]) =>
    [...streamCommunityKeys.all, 'bulk', [...streamIds].sort((a, b) => a - b).join(',')] as const,
}

export function useBulkStreamCommunity(streamIds: number[]) {
  const normalizedIds = [...new Set(streamIds.filter((id) => id > 0))].sort((a, b) => a - b)

  return useQuery({
    queryKey: streamCommunityKeys.bulk(normalizedIds),
    queryFn: () => streamCommunityApi.getBulk(normalizedIds),
    enabled: normalizedIds.length > 0,
    staleTime: 20_000,
  })
}

export function useInvalidateStreamCommunity() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: streamCommunityKeys.all })
}
