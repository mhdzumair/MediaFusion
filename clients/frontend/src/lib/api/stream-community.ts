import { apiClient } from './client'
import type { StreamVoteSummary } from './voting'

export interface StreamCommunityStats {
  stream_id: number
  upvotes: number
  downvotes: number
  score: number
  score_percent: number
  user_vote?: {
    vote_type: string
    vote: number
    quality_status?: string | null
    comment?: string | null
  } | null
  rating_up: number
  rating_down: number
  rating_score: number
  rating_total: number
  user_vote_int: number | null
  issue_report_count: number
  user_has_issue_report: boolean | null
  watched_count: number
}

export interface BulkStreamCommunityResponse {
  streams: Record<string, StreamCommunityStats>
}

export function communityStatsToVoteSummary(stats: StreamCommunityStats): StreamVoteSummary {
  return {
    stream_id: stats.stream_id,
    upvotes: stats.upvotes,
    downvotes: stats.downvotes,
    score: stats.score,
    score_percent: stats.score_percent,
    user_vote: stats.user_vote_int,
    quality_status: stats.user_vote?.quality_status ?? null,
    comment: stats.user_vote?.comment ?? null,
  }
}

export const streamCommunityApi = {
  getBulk: async (streamIds: number[]): Promise<BulkStreamCommunityResponse> => {
    return apiClient.post<BulkStreamCommunityResponse>('/streams/community/bulk', {
      stream_ids: streamIds,
    })
  },
}
