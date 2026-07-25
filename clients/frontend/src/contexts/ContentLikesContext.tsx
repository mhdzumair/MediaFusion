/* eslint-disable react-refresh/only-export-components */
import { createContext, useContext, useMemo, type ReactNode } from 'react'
import { useBulkContentLikes } from '@/hooks/useContentLikes'
import type { ContentLikeSummary } from '@/lib/api/voting'

interface ContentLikesContextValue {
  stats: Record<string, ContentLikeSummary>
  isLoading: boolean
}

const ContentLikesContext = createContext<ContentLikesContextValue>({
  stats: {},
  isLoading: false,
})

export function ContentLikesProvider({ mediaIds, children }: { mediaIds: number[]; children: ReactNode }) {
  const normalizedIds = useMemo(
    () => [...new Set(mediaIds.filter((id) => id > 0))].sort((a, b) => a - b).slice(0, 100),
    [mediaIds],
  )
  const { data, isLoading } = useBulkContentLikes(normalizedIds)
  const value = useMemo(
    () => ({
      stats: data?.media ?? {},
      isLoading,
    }),
    [data?.media, isLoading],
  )

  return <ContentLikesContext.Provider value={value}>{children}</ContentLikesContext.Provider>
}

export function useContentLikesStats(mediaId: number | undefined) {
  const { stats, isLoading } = useContext(ContentLikesContext)
  if (mediaId === undefined) {
    return { stats: undefined, isLoading: false }
  }
  return {
    stats: stats[String(mediaId)],
    isLoading,
  }
}
