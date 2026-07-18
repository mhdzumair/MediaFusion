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

export interface TelegramSessionStatus {
  connected: boolean
  telegram_account_id?: number
  linked_at?: string
  last_used_at?: string
  api_configured: boolean
}

export interface TelegramSessionStartResponse {
  status: 'code_sent'
  phone: string
  message: string
}

export interface TelegramSessionVerifyResponse {
  status: 'connected' | 'password_required'
  telegram_account_id?: number
  hint?: string | null
}

export interface TelegramSessionDeleteResponse {
  success: boolean
  message: string
}

export interface TelegramScrapingChannel {
  id: string
  name: string
  enabled: boolean
  priority?: number
  is_public?: boolean
  stream_count?: number
}

export interface TelegramConfigResponse {
  enabled: boolean
  channels: TelegramScrapingChannel[]
  account_linked: boolean
  telegram_user_id?: string
  session_connected: boolean
  session_telegram_account_id?: number
}

export interface TelegramDialog {
  id: string
  name: string
  kind: string
  scrapable: boolean
  is_public: boolean
  has_photo: boolean
}

export const telegramApi = {
  linkAccount: async (token: string, replaceExisting = false): Promise<TelegramLinkResponse> => {
    const query = new URLSearchParams({
      token,
      replace_existing: replaceExisting ? 'true' : 'false',
    })
    return apiClient.get<TelegramLinkResponse>(`/telegram/login?${query.toString()}`)
  },

  unlinkAccount: async (): Promise<TelegramUnlinkResponse> => {
    return apiClient.delete<TelegramUnlinkResponse>('/telegram/unlink')
  },

  getConfig: async (): Promise<TelegramConfigResponse> => {
    return apiClient.get<TelegramConfigResponse>('/telegram/config')
  },

  getSessionStatus: async (): Promise<TelegramSessionStatus> => {
    return apiClient.get<TelegramSessionStatus>('/telegram/session/status')
  },

  startSessionLogin: async (phone: string): Promise<TelegramSessionStartResponse> => {
    return apiClient.post<TelegramSessionStartResponse>('/telegram/session/start', { phone })
  },

  verifySessionCode: async (code: string): Promise<TelegramSessionVerifyResponse> => {
    return apiClient.post<TelegramSessionVerifyResponse>('/telegram/session/verify', { code })
  },

  verifySessionPassword: async (password: string): Promise<TelegramSessionVerifyResponse> => {
    return apiClient.post<TelegramSessionVerifyResponse>('/telegram/session/password', { password })
  },

  deleteSession: async (): Promise<TelegramSessionDeleteResponse> => {
    return apiClient.delete<TelegramSessionDeleteResponse>('/telegram/session')
  },

  listDialogs: async (limit = 60): Promise<{ dialogs: TelegramDialog[] }> => {
    return apiClient.get<{ dialogs: TelegramDialog[] }>(`/telegram/dialogs?limit=${limit}`)
  },

  addChannel: async (id: string, name: string): Promise<TelegramScrapingChannel> => {
    return apiClient.post<TelegramScrapingChannel>('/telegram/channels', { id, name })
  },

  removeChannel: async (id: string): Promise<void> => {
    await apiClient.delete(`/telegram/channels/${encodeURIComponent(id)}`)
  },

  getDialogPhotoBlob: async (id: string): Promise<Blob> => {
    return apiClient.getBlob(`/telegram/dialogs/${encodeURIComponent(id)}/photo`)
  },

  triggerScrape: async (payload: {
    channel?: string
    scrape_all?: boolean
    message_limit?: number
    scrape_all_messages?: boolean
  }): Promise<{ status: string; message: string; channels?: number }> => {
    return apiClient.post('/telegram/scrape', payload)
  },
}
