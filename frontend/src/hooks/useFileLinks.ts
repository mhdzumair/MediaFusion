import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fileLinksApi,
  type StreamsNeedingAnnotationParams,
  type BulkFileLinkUpdateRequest,
} from '@/lib/api/fileLinks'

// Query keys for cache management
export const fileLinksKeys = {
  all: ['fileLinks'] as const,
  needsAnnotation: (params: StreamsNeedingAnnotationParams) =>
    [...fileLinksKeys.all, 'needsAnnotation', params] as const,
  streamFiles: (streamId: number, mediaId: number) =>
    [...fileLinksKeys.all, 'streamFiles', streamId, mediaId] as const,
}

/**
 * Hook to fetch streams that need file annotation (moderator only)
 */
export function useStreamsNeedingAnnotation(params: StreamsNeedingAnnotationParams = {}) {
  return useQuery({
    queryKey: fileLinksKeys.needsAnnotation(params),
    queryFn: () => fileLinksApi.getStreamsNeedingAnnotation(params),
  })
}

/**
 * Hook to fetch file links for a specific stream and media
 */
export function useStreamFileLinks(streamId: number, mediaId: number) {
  return useQuery({
    queryKey: fileLinksKeys.streamFiles(streamId, mediaId),
    queryFn: () => fileLinksApi.getStreamFileLinks(streamId, mediaId),
    enabled: !!streamId && !!mediaId,
  })
}

/**
 * Hook to update file links (direct update, not through suggestions)
 */
export function useUpdateFileLinks() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (request: BulkFileLinkUpdateRequest) =>
      fileLinksApi.updateFileLinks(request),
    onSuccess: (_data, variables) => {
      // Invalidate the streams needing annotation list
      queryClient.invalidateQueries({
        queryKey: fileLinksKeys.all,
      })
      // Also invalidate the specific stream's file links
      queryClient.invalidateQueries({
        queryKey: fileLinksKeys.streamFiles(variables.stream_id, variables.media_id),
      })
    },
  })
}

