/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useBulkStreamCommunity } from '@/hooks/useStreamCommunity'
import type { StreamCommunityStats } from '@/lib/api/stream-community'

interface StreamCommunityContextValue {
  stats: Record<string, StreamCommunityStats>
  isLoading: boolean
}

const StreamCommunityContext = createContext<StreamCommunityContextValue>({
  stats: {},
  isLoading: false,
})

export function StreamCommunityProvider({ streamIds, children }: { streamIds: number[]; children: ReactNode }) {
  const normalizedIds = useMemo(
    () => [...new Set(streamIds.filter((id) => id > 0))].sort((a, b) => a - b).slice(0, 100),
    [streamIds],
  )
  const { data, isLoading } = useBulkStreamCommunity(normalizedIds)
  const value = useMemo(
    () => ({
      stats: data?.streams ?? {},
      isLoading,
    }),
    [data?.streams, isLoading],
  )

  return <StreamCommunityContext.Provider value={value}>{children}</StreamCommunityContext.Provider>
}

export function useStreamCommunityStats(streamId: number | undefined) {
  const { stats, isLoading } = useContext(StreamCommunityContext)
  if (streamId === undefined) {
    return { stats: undefined, isLoading: false }
  }
  return {
    stats: stats[String(streamId)],
    isLoading,
  }
}
