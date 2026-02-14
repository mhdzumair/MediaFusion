import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { 
  suggestionsApi, 
  type SuggestionCreateRequest, 
  type SuggestionReviewRequest,
  type SuggestionListParams,
  type PendingSuggestionParams 
} from '@/lib/api'

// Query keys
export const suggestionKeys = {
  all: ['suggestions'] as const,
  list: (params?: SuggestionListParams) => [...suggestionKeys.all, 'list', params] as const,
  pending: (params?: PendingSuggestionParams) => [...suggestionKeys.all, 'pending', params] as const,
  detail: (id: string) => [...suggestionKeys.all, 'detail', id] as const,
  stats: () => [...suggestionKeys.all, 'stats'] as const,
}

// List user's suggestions
export function useSuggestions(params?: SuggestionListParams) {
  return useQuery({
    queryKey: suggestionKeys.list(params),
    queryFn: () => suggestionsApi.list(params),
  })
}

// Get single suggestion
export function useSuggestion(suggestionId: string | undefined) {
  return useQuery({
    queryKey: suggestionKeys.detail(suggestionId!),
    queryFn: () => suggestionsApi.get(suggestionId!),
    enabled: !!suggestionId,
  })
}

// List pending suggestions (moderator)
export function usePendingSuggestions(params?: PendingSuggestionParams) {
  return useQuery({
    queryKey: suggestionKeys.pending(params),
    queryFn: () => suggestionsApi.listPending(params),
  })
}

// Get suggestion stats
export function useSuggestionStats() {
  return useQuery({
    queryKey: suggestionKeys.stats(),
    queryFn: () => suggestionsApi.getStats(),
  })
}

// Create suggestion
export function useCreateSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ mediaId, data }: { mediaId: number; data: SuggestionCreateRequest }) =>
      suggestionsApi.create(mediaId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: suggestionKeys.all })
    },
  })
}

// Delete suggestion
export function useDeleteSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (suggestionId: string) => suggestionsApi.delete(suggestionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: suggestionKeys.all })
    },
  })
}

// Review suggestion (moderator)
export function useReviewSuggestion() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ suggestionId, data }: { suggestionId: string; data: SuggestionReviewRequest }) =>
      suggestionsApi.review(suggestionId, data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: suggestionKeys.all })
    },
  })
}

