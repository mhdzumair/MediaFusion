import { useQuery, useQueryClient } from '@tanstack/react-query'
import { votingApi } from '@/lib/api/voting'

export const contentLikesKeys = {
  all: ['content-likes'] as const,
  bulk: (mediaIds: number[]) =>
    [...contentLikesKeys.all, 'bulk', [...mediaIds].sort((a, b) => a - b).join(',')] as const,
}

export function useBulkContentLikes(mediaIds: number[]) {
  const normalizedIds = [...new Set(mediaIds.filter((id) => id > 0))].sort((a, b) => a - b)

  return useQuery({
    queryKey: contentLikesKeys.bulk(normalizedIds),
    queryFn: () => votingApi.getBulkContentLikes(normalizedIds),
    enabled: normalizedIds.length > 0,
    staleTime: 20_000,
  })
}

export function useInvalidateContentLikes() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: contentLikesKeys.all })
}
