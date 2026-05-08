import { useQuery, useMutation, useQueryClient, useInfiniteQuery } from '@tanstack/react-query'
import { libraryApi, type LibraryListParams, type LibraryItemCreate, type CatalogType } from '@/lib/api'

// Query keys
export const libraryKeys = {
  all: ['library'] as const,
  list: (params: LibraryListParams) => [...libraryKeys.all, 'list', params] as const,
  stats: () => [...libraryKeys.all, 'stats'] as const,
  item: (id: number) => [...libraryKeys.all, 'item', id] as const,
  check: (mediaId: number) => [...libraryKeys.all, 'check', mediaId] as const,
}

// Get user's library with pagination
export function useLibrary(params: LibraryListParams = {}) {
  return useQuery({
    queryKey: libraryKeys.list(params),
    queryFn: () => libraryApi.getLibrary(params),
    staleTime: 2 * 60 * 1000, // 2 minutes
  })
}

// Get library with infinite loading
export function useInfiniteLibrary(params: Omit<LibraryListParams, 'page'> = {}) {
  return useInfiniteQuery({
    queryKey: [...libraryKeys.list(params), 'infinite'],
    queryFn: ({ pageParam = 1 }) => libraryApi.getLibrary({ ...params, page: pageParam }),
    getNextPageParam: (lastPage) => (lastPage.has_more ? lastPage.page + 1 : undefined),
    initialPageParam: 1,
    staleTime: 2 * 60 * 1000, // 2 minutes
  })
}

// Get library statistics
export function useLibraryStats() {
  return useQuery({
    queryKey: libraryKeys.stats(),
    queryFn: () => libraryApi.getStats(),
    staleTime: 2 * 60 * 1000, // 2 minutes
  })
}

// Get a specific library item
export function useLibraryItem(itemId: number) {
  return useQuery({
    queryKey: libraryKeys.item(itemId),
    queryFn: () => libraryApi.getLibraryItem(itemId),
    enabled: !!itemId,
  })
}

// Check if item is in library by media_id
export function useLibraryCheck(mediaId: number) {
  return useQuery({
    queryKey: libraryKeys.check(mediaId),
    queryFn: () => libraryApi.checkInLibrary(mediaId),
    enabled: !!mediaId,
    staleTime: 1 * 60 * 1000, // 1 minute
  })
}

// Add item to library
export function useAddToLibrary() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (data: LibraryItemCreate) => libraryApi.addToLibrary(data),
    onSuccess: (_, variables) => {
      // Invalidate library list and stats
      queryClient.invalidateQueries({ queryKey: libraryKeys.all })
      // Update the check query immediately
      queryClient.setQueryData(libraryKeys.check(variables.media_id), {
        in_library: true,
      })
    },
    onError: (error: unknown, variables) => {
      // If 409 Conflict, item is already in library - update the check query
      const apiError = error as { status?: number; message?: string }
      if (apiError?.status === 409 || apiError?.message?.includes('409')) {
        queryClient.setQueryData(libraryKeys.check(variables.media_id), {
          in_library: true,
        })
        // Don't throw - just sync the state
        return
      }
      // Re-fetch the check query to get the correct state
      queryClient.invalidateQueries({ queryKey: libraryKeys.check(variables.media_id) })
    },
  })
}

// Remove item from library by item ID
export function useRemoveFromLibrary() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (itemId: number) => libraryApi.removeFromLibrary(itemId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: libraryKeys.all })
    },
  })
}

// Remove item from library by media_id
export function useRemoveFromLibraryByMediaId() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (mediaId: number) => libraryApi.removeFromLibraryByMediaId(mediaId),
    onSuccess: (_, mediaId) => {
      queryClient.invalidateQueries({ queryKey: libraryKeys.all })
      // Update the check query immediately
      queryClient.setQueryData(libraryKeys.check(mediaId), {
        in_library: false,
      })
    },
  })
}

// Export types
export type { LibraryListParams, LibraryItemCreate, CatalogType }
