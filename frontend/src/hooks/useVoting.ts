import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { votingApi, type StreamVoteRequest } from '@/lib/api'

// Query keys
export const votingKeys = {
  all: ['voting'] as const,
  streamVotes: (streamId: number) => [...votingKeys.all, 'stream', streamId] as const,
  bulkStreamVotes: (streamIds: number[]) => [...votingKeys.all, 'streams', streamIds.sort().join(',')] as const,
  contentLikes: (mediaId: number) => [...votingKeys.all, 'content', mediaId] as const,
}

// Get stream votes
export function useStreamVotes(streamId: number | undefined) {
  return useQuery({
    queryKey: votingKeys.streamVotes(streamId!),
    queryFn: () => votingApi.getStreamVotes(streamId!),
    enabled: streamId !== undefined,
  })
}

// Get bulk stream votes
export function useBulkStreamVotes(streamIds: number[]) {
  return useQuery({
    queryKey: votingKeys.bulkStreamVotes(streamIds),
    queryFn: () => votingApi.getBulkStreamVotes(streamIds),
    enabled: streamIds.length > 0,
  })
}

// Vote on stream
export function useVoteOnStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, data }: { streamId: number; data: StreamVoteRequest }) =>
      votingApi.voteOnStream(streamId, data),
    onSuccess: (_, { streamId }) => {
      queryClient.invalidateQueries({ queryKey: votingKeys.streamVotes(streamId) })
      // Also invalidate any bulk queries that might include this stream
      queryClient.invalidateQueries({
        queryKey: votingKeys.all,
        predicate: (query) => query.queryKey[1] === 'streams',
      })
    },
  })
}

// Remove stream vote
export function useRemoveStreamVote() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => votingApi.removeStreamVote(streamId),
    onSuccess: (_, streamId) => {
      queryClient.invalidateQueries({ queryKey: votingKeys.streamVotes(streamId) })
      queryClient.invalidateQueries({
        queryKey: votingKeys.all,
        predicate: (query) => query.queryKey[1] === 'streams',
      })
    },
  })
}

// Get content likes (popularity) - uses media_id (internal ID)
export function useContentLikes(mediaId: number | undefined) {
  return useQuery({
    queryKey: votingKeys.contentLikes(mediaId!),
    queryFn: () => votingApi.getContentLikes(mediaId!),
    enabled: !!mediaId,
  })
}

// Like content - uses media_id
export function useLikeContent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: number) => votingApi.likeContent(mediaId),
    onSuccess: (_, mediaId) => {
      queryClient.invalidateQueries({ queryKey: votingKeys.contentLikes(mediaId) })
    },
  })
}

// Unlike content - uses media_id
export function useUnlikeContent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: number) => votingApi.unlikeContent(mediaId),
    onSuccess: (_, mediaId) => {
      queryClient.invalidateQueries({ queryKey: votingKeys.contentLikes(mediaId) })
    },
  })
}
