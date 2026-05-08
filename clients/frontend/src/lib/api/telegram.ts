import { apiClient } from './client'

export interface TelegramLinkResponse {
  success: boolean
  message: string
  requires_confirmation?: boolean
}

export interface TelegramUnlinkResponse {
  success: boolean
  message: string
}

export const telegramApi = {
  /**
   * Link Telegram account to MediaFusion account using login token
   */
  linkAccount: async (token: string, replaceExisting = false): Promise<TelegramLinkResponse> => {
    const query = new URLSearchParams({
      token,
      replace_existing: replaceExisting ? 'true' : 'false',
    })
    return apiClient.get<TelegramLinkResponse>(`/telegram/login?${query.toString()}`)
  },

  /**
   * Unlink Telegram account from MediaFusion account
   */
  unlinkAccount: async (): Promise<TelegramUnlinkResponse> => {
    return apiClient.delete<TelegramUnlinkResponse>('/telegram/unlink')
  },
}
