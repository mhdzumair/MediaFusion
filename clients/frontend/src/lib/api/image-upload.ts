import { apiClient } from './client'

export interface ImageUploadResponse {
  url: string
  key: string
  content_type: string
  size: number
}

export const imageUploadApi = {
  upload: async (imageFile: File): Promise<ImageUploadResponse> => {
    const formData = new FormData()
    formData.append('image', imageFile)
    return apiClient.upload<ImageUploadResponse>('/import/images/upload', formData)
  },
}
