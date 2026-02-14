import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  streamSuggestionsApi,
  type StreamSuggestionCreateRequest,
  type StreamSuggestionReviewRequest,
  type StreamSuggestionListParams,
} from '@/lib/api'

// Query keys
export const streamSuggestionKeys = {
  all: ['stream-suggestions'] as const,
  stream: (streamId: string) => [...streamSuggestionKeys.all, 'stream', streamId] as const,
  my: () => [...streamSuggestionKeys.all, 'my'] as const,
  pending: () => [...streamSuggestionKeys.all, 'pending'] as const,
  stats: () => [...streamSuggestionKeys.all, 'stats'] as const,
}

// Get suggestions for a stream
export function useStreamSuggestions(streamId: number | undefined, params: StreamSuggestionListParams = {}) {
  return useQuery({
    queryKey: [...streamSuggestionKeys.stream(String(streamId!)), params],
    queryFn: () => streamSuggestionsApi.getStreamSuggestions(streamId!, params),
    enabled: streamId !== undefined,
  })
}

// Get user's own stream suggestions
export function useMyStreamSuggestions(params: StreamSuggestionListParams = {}) {
  return useQuery({
    queryKey: [...streamSuggestionKeys.my(), params],
    queryFn: () => streamSuggestionsApi.getMySuggestions(params),
  })
}

// Params for pending stream suggestions (includes suggestion_type filter)
interface PendingStreamSuggestionParams extends Omit<StreamSuggestionListParams, 'status'> {
  suggestion_type?: string
}

// Get pending suggestions (moderator)
export function usePendingStreamSuggestions(params: PendingStreamSuggestionParams = {}) {
  return useQuery({
    queryKey: [...streamSuggestionKeys.pending(), params],
    queryFn: () => streamSuggestionsApi.getPendingSuggestions(params),
  })
}

// Get stats (moderator)
export function useStreamSuggestionStats() {
  return useQuery({
    queryKey: streamSuggestionKeys.stats(),
    queryFn: () => streamSuggestionsApi.getStats(),
  })
}

// Create stream suggestion
export function useCreateStreamSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, data }: { streamId: number; data: StreamSuggestionCreateRequest }) =>
      streamSuggestionsApi.createSuggestion(streamId, data),
    onSuccess: (_, { streamId }) => {
      queryClient.invalidateQueries({ queryKey: streamSuggestionKeys.stream(String(streamId)) })
      queryClient.invalidateQueries({ queryKey: streamSuggestionKeys.pending() })
      queryClient.invalidateQueries({ queryKey: streamSuggestionKeys.stats() })
    },
  })
}

// Review stream suggestion (moderator)
export function useReviewStreamSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ suggestionId, data }: { suggestionId: string; data: StreamSuggestionReviewRequest }) =>
      streamSuggestionsApi.reviewSuggestion(suggestionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: streamSuggestionKeys.all })
    },
  })
}

// Delete stream suggestion
export function useDeleteStreamSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (suggestionId: string) => streamSuggestionsApi.deleteSuggestion(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: streamSuggestionKeys.all })
    },
  })
}
