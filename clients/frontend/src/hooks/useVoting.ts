import { useMutation, useQueryClient } from '@tanstack/react-query'
import { votingApi, type StreamVoteRequest } from '@/lib/api'

import { streamCommunityKeys } from './useStreamCommunity'
import { contentLikesKeys } from './useContentLikes'

// Query keys
export const votingKeys = {
  all: ['voting'] as const,
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

// Like content - uses media_id
export function useLikeContent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: number) => votingApi.likeContent(mediaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contentLikesKeys.all })
    },
  })
}

// Unlike content - uses media_id
export function useUnlikeContent() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: number) => votingApi.unlikeContent(mediaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: contentLikesKeys.all })
    },
  })
}
