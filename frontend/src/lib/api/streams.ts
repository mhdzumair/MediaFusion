import { apiClient } from './client'

export const streamsApi = {
  deleteStream: async (streamId: number): Promise<{ message: string }> => {
    return apiClient.delete<{ message: string }>(`/streams/${streamId}`)
  },
}
