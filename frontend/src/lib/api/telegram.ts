import { apiClient } from './client'

export interface TelegramLinkResponse {
  success: boolean
  message: string
}

export const telegramApi = {
  /**
   * Link Telegram account to MediaFusion account using login token
   */
  linkAccount: async (token: string): Promise<TelegramLinkResponse> => {
    return apiClient.get<TelegramLinkResponse>(`/telegram/login?token=${encodeURIComponent(token)}`)
  },
}
