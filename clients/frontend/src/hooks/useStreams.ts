import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { streamsApi, type MyStreamsListParams, type UpdateMyStreamRequest } from '@/lib/api/streams'

export const myStreamsKeys = {
  all: ['streams', 'mine'] as const,
  list: (params?: MyStreamsListParams) => [...myStreamsKeys.all, params] as const,
}

export function useMyStreams(params?: MyStreamsListParams) {
  return useQuery({
    queryKey: myStreamsKeys.list(params),
    queryFn: () => streamsApi.listMyStreams(params),
  })
}

export function useUpdateMyStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: ({ streamId, body }: { streamId: number; body: UpdateMyStreamRequest }) =>
      streamsApi.updateMyStream(streamId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: myStreamsKeys.all })
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
  })
}

export function useBlockMyStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => streamsApi.blockMyStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: myStreamsKeys.all })
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
  })
}

export function useDeleteStream() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: (streamId: number) => streamsApi.deleteStream(streamId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: myStreamsKeys.all })
      queryClient.invalidateQueries({ queryKey: ['admin', 'torrent-streams'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'tv-streams'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'stats'] })
      queryClient.invalidateQueries({ queryKey: ['admin', 'metadata'] })
      queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
  })
}
