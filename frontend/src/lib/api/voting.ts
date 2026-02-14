import { apiClient } from './client'

// Types
export type VoteType = 'up' | 'down'
export type QualityStatus = 'working' | 'broken' | 'good_quality' | 'poor_quality'

export interface StreamVoteRequest {
  vote_type: VoteType
  quality_status?: QualityStatus
  comment?: string
}

export interface StreamVoteResponse {
  id: string
  stream_id: string
  vote_type: VoteType
  quality_status: QualityStatus | null
  comment: string | null
  created_at: string
}

export interface StreamVoteSummary {
  stream_id: string
  upvotes: number
  downvotes: number
  working_count: number
  broken_count: number
  good_quality_count: number
  poor_quality_count: number
  user_vote: StreamVoteResponse | null
  score_percent: number
}

export interface BulkStreamVoteSummary {
  summaries: Record<string, StreamVoteSummary>
}

// Content likes (popularity)
export interface ContentLikeResponse {
  id: number
  media_id: number
  liked: boolean
  created_at: string
}

export interface ContentLikeSummary {
  media_id: number
  likes_count: number
  user_liked: boolean
}

// API functions
export const votingApi = {
  // Stream voting
  voteOnStream: async (streamId: number, data: StreamVoteRequest): Promise<StreamVoteResponse> => {
    return apiClient.post<StreamVoteResponse>(`/streams/${streamId}/vote`, data)
  },

  removeStreamVote: async (streamId: number): Promise<void> => {
    await apiClient.delete(`/streams/${streamId}/vote`)
  },

  getStreamVotes: async (streamId: number): Promise<StreamVoteSummary> => {
    return apiClient.get<StreamVoteSummary>(`/streams/${streamId}/votes`)
  },

  getBulkStreamVotes: async (streamIds: number[]): Promise<BulkStreamVoteSummary> => {
    return apiClient.post<BulkStreamVoteSummary>('/streams/votes/bulk', streamIds)
  },

  // Content likes (popularity) - uses media_id (internal ID)
  likeContent: async (mediaId: number): Promise<ContentLikeResponse> => {
    return apiClient.post<ContentLikeResponse>(`/content/${mediaId}/like`)
  },

  unlikeContent: async (mediaId: number): Promise<void> => {
    await apiClient.delete(`/content/${mediaId}/like`)
  },

  getContentLikes: async (mediaId: number): Promise<ContentLikeSummary> => {
    return apiClient.get<ContentLikeSummary>(`/content/${mediaId}/likes`)
  },
}
