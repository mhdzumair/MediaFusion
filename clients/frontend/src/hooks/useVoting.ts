import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { votingApi, type StreamVoteRequest } from '@/lib/api'

import { streamCommunityKeys } from './useStreamCommunity'

// Query keys
export const votingKeys = {
  all: ['voting'] as const,
  contentLikes: (mediaId: number) => [...votingKeys.all, 'content', mediaId] as const,
}

// Vote on stream
export function useVoteOnStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, data }: { streamId: number; data: StreamVoteRequest }) =>
      votingApi.voteOnStream(streamId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: streamCommunityKeys.all })
    },
  })
}

// Remove stream vote
export function useRemoveStreamVote() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => votingApi.removeStreamVote(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: streamCommunityKeys.all })
    },
  })
}

// Get content likes
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
