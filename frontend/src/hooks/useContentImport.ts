import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  contentImportApi,
  type MagnetAnalyzeRequest,
  type M3UImportOverride,
  type XtreamCredentials,
  type XtreamImportRequest,
  type IPTVSourceUpdateRequest,
  type TorrentImportRequest,
  type ImportJobStatus,
  type TorrentMetaType,
} from '@/lib/api'

export function useAnalyzeMagnet() {
  return useMutation({
    mutationFn: (data: MagnetAnalyzeRequest) => contentImportApi.analyzeMagnet(data),
  })
}

export function useAnalyzeTorrent() {
  return useMutation({
    mutationFn: ({ file, metaType }: { file: File; metaType: TorrentMetaType }) =>
      contentImportApi.analyzeTorrent(file, metaType),
  })
}

export function useImportMagnet() {
  return useMutation({
    mutationFn: (data: Omit<TorrentImportRequest, 'torrent_file'> & { magnet_link: string }) =>
      contentImportApi.importMagnet(data),
  })
}

export function useImportTorrent() {
  return useMutation({
    mutationFn: (data: Omit<TorrentImportRequest, 'magnet_link'> & { torrent_file: File }) =>
      contentImportApi.importTorrent(data),
  })
}

export function useAnalyzeM3U() {
  return useMutation({
    mutationFn: (data: { m3u_url?: string; m3u_file?: File }) => contentImportApi.analyzeM3U(data),
  })
}

export function useImportM3U() {
  return useMutation({
    mutationFn: (data: {
      m3u_url?: string
      m3u_file?: File
      redis_key?: string
      source?: string
      is_public?: boolean
      overrides?: M3UImportOverride[]
      save_source?: boolean
      source_name?: string
    }) => contentImportApi.importM3U(data),
  })
}

// ============================================
// Xtream Codes Hooks
// ============================================

export function useAnalyzeXtream() {
  return useMutation({
    mutationFn: (credentials: XtreamCredentials) => contentImportApi.analyzeXtream(credentials),
  })
}

export function useImportXtream() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (data: XtreamImportRequest) => contentImportApi.importXtream(data),
    onSuccess: () => {
      // Invalidate sources list after import
      queryClient.invalidateQueries({ queryKey: ['iptv-sources'] })
    },
  })
}

// ============================================
// IPTV Settings Hook
// ============================================

export function useIPTVImportSettings() {
  return useQuery({
    queryKey: ['iptv-import-settings'],
    queryFn: () => contentImportApi.getIPTVImportSettings(),
    staleTime: 5 * 60 * 1000, // Cache for 5 minutes
  })
}

// ============================================
// IPTV Source Management Hooks
// ============================================

export function useIPTVSources() {
  return useQuery({
    queryKey: ['iptv-sources'],
    queryFn: () => contentImportApi.listSources(),
  })
}

export function useIPTVSource(sourceId: number) {
  return useQuery({
    queryKey: ['iptv-source', sourceId],
    queryFn: () => contentImportApi.getSource(sourceId),
    enabled: sourceId > 0,
  })
}

export function useUpdateIPTVSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: ({ sourceId, data }: { sourceId: number; data: IPTVSourceUpdateRequest }) =>
      contentImportApi.updateSource(sourceId, data),
    onSuccess: (_, { sourceId }) => {
      queryClient.invalidateQueries({ queryKey: ['iptv-sources'] })
      queryClient.invalidateQueries({ queryKey: ['iptv-source', sourceId] })
    },
  })
}

export function useDeleteIPTVSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: number) => contentImportApi.deleteSource(sourceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['iptv-sources'] })
    },
  })
}

export function useSyncIPTVSource() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (sourceId: number) => contentImportApi.syncSource(sourceId),
    onSuccess: (_, sourceId) => {
      queryClient.invalidateQueries({ queryKey: ['iptv-sources'] })
      queryClient.invalidateQueries({ queryKey: ['iptv-source', sourceId] })
    },
  })
}

// ============================================
// Import Job Status Hook
// ============================================

/**
 * Hook to poll for import job status.
 *
 * @param jobId - The job ID to poll for (or null to disable polling)
 * @param options - Options for polling behavior
 */
export function useImportJobStatus(
  jobId: string | null,
  options: {
    onComplete?: (status: ImportJobStatus) => void
    onError?: (status: ImportJobStatus) => void
  } = {},
) {
  const queryClient = useQueryClient()
  const callbackFiredRef = useRef(false)

  // Reset callback fired flag when jobId changes
  useEffect(() => {
    callbackFiredRef.current = false
  }, [jobId])

  const query = useQuery({
    queryKey: ['import-job', jobId],
    queryFn: () => contentImportApi.getImportJobStatus(jobId!),
    enabled: !!jobId,
    refetchInterval: (data) => {
      const result = data.state.data
      if (!result) return 2000 // Initial poll every 2 seconds

      // Stop polling when complete or failed or not found
      if (result.status === 'completed' || result.status === 'failed' || result.status === 'not_found') {
        return false
      }

      // Continue polling every 2 seconds while processing
      return 2000
    },
    staleTime: 1000,
  })

  // Handle callbacks in useEffect to ensure they're only called once
  useEffect(() => {
    if (!query.data || callbackFiredRef.current) return

    if (query.data.status === 'completed') {
      callbackFiredRef.current = true
      if (options.onComplete) {
        options.onComplete(query.data)
      }
      // Invalidate sources after successful import
      queryClient.invalidateQueries({ queryKey: ['iptv-sources'] })
    } else if (query.data.status === 'failed') {
      callbackFiredRef.current = true
      if (options.onError) {
        options.onError(query.data)
      }
    }
  }, [query.data, options, queryClient])

  return query
}
