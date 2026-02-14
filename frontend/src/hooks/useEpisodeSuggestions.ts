import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { 
  episodeSuggestionsApi, 
  type EpisodeSuggestionCreateRequest, 
  type EpisodeSuggestionReviewRequest,
  type EpisodeSuggestionListParams,
  type PendingEpisodeSuggestionParams 
} from '@/lib/api'

// Query keys
export const episodeSuggestionKeys = {
  all: ['episode-suggestions'] as const,
  list: (params?: EpisodeSuggestionListParams) => [...episodeSuggestionKeys.all, 'list', params] as const,
  pending: (params?: PendingEpisodeSuggestionParams) => [...episodeSuggestionKeys.all, 'pending', params] as const,
  detail: (id: string) => [...episodeSuggestionKeys.all, 'detail', id] as const,
  stats: () => [...episodeSuggestionKeys.all, 'stats'] as const,
}

// List user's episode suggestions
export function useEpisodeSuggestions(params?: EpisodeSuggestionListParams) {
  return useQuery({
    queryKey: episodeSuggestionKeys.list(params),
    queryFn: () => episodeSuggestionsApi.list(params),
  })
}

// Get single episode suggestion
export function useEpisodeSuggestion(suggestionId: string | undefined) {
  return useQuery({
    queryKey: episodeSuggestionKeys.detail(suggestionId!),
    queryFn: () => episodeSuggestionsApi.get(suggestionId!),
    enabled: !!suggestionId,
  })
}

// List pending episode suggestions (moderator)
export function usePendingEpisodeSuggestions(params?: PendingEpisodeSuggestionParams) {
  return useQuery({
    queryKey: episodeSuggestionKeys.pending(params),
    queryFn: () => episodeSuggestionsApi.listPending(params),
  })
}

// Get episode suggestion stats
export function useEpisodeSuggestionStats() {
  return useQuery({
    queryKey: episodeSuggestionKeys.stats(),
    queryFn: () => episodeSuggestionsApi.getStats(),
  })
}

// Create episode suggestion
export function useCreateEpisodeSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ episodeId, data }: { episodeId: number; data: EpisodeSuggestionCreateRequest }) =>
      episodeSuggestionsApi.create(episodeId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: episodeSuggestionKeys.all })
    },
  })
}

// Delete episode suggestion
export function useDeleteEpisodeSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (suggestionId: string) => episodeSuggestionsApi.delete(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: episodeSuggestionKeys.all })
    },
  })
}

// Review episode suggestion (moderator)
export function useReviewEpisodeSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ suggestionId, data }: { suggestionId: string; data: EpisodeSuggestionReviewRequest }) =>
      episodeSuggestionsApi.review(suggestionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: episodeSuggestionKeys.all })
    },
  })
}

// Bulk review episode suggestions (moderator)
export function useBulkReviewEpisodeSuggestions() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ suggestionIds, action, reviewNotes }: { 
      suggestionIds: string[]
      action: 'approve' | 'reject'
      reviewNotes?: string 
    }) => episodeSuggestionsApi.bulkReview(suggestionIds, action, reviewNotes),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: episodeSuggestionKeys.all })
    },
  })
}
