import { useMutation, useQueryClient } from '@tanstack/react-query'
import { streamsApi } from '@/lib/api'

export function useDeleteStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => streamsApi.deleteStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['admin', 'torrent-streams'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'tv-streams'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'stats'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'metadata'] })
    },
  })
}
