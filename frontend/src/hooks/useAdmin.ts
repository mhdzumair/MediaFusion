import { useMutation, useQueryClient } from '@tanstack/react-query'
import { adminApi } from '@/lib/api'

// ============================================
// Query Keys
// ============================================

const ADMIN_METADATA_KEY = ['admin', 'metadata']
const ADMIN_TORRENT_STREAMS_KEY = ['admin', 'torrent-streams']

export function useDeleteMetadata() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (metaId: number) => adminApi.deleteMetadata(metaId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_METADATA_KEY })
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
    },
  })
}

export function useBlockTorrentStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => adminApi.blockTorrentStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ADMIN_TORRENT_STREAMS_KEY })
    },
  })
}
