import { apiClient } from './client'
import type { AuthResponse, LoginRequest, RegisterRequest, User } from '@/types'

export interface UserUpdateRequest {
  username?: string
  contribute_anonymously?: boolean
}

export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}

export const authApi = {
  login: async (data: LoginRequest): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/login', data)
    apiClient.setTokens(response.access_token, response.refresh_token)
    return response
  },

  register: async (data: RegisterRequest): Promise<AuthResponse> => {
    const response = await apiClient.post<AuthResponse>('/auth/register', data)
    apiClient.setTokens(response.access_token, response.refresh_token)
    return response
  },

  logout: async (): Promise<void> => {
    try {
      await apiClient.post('/auth/logout')
    } finally {
      apiClient.clearTokens()
    }
  },

  refreshToken: async (): Promise<AuthResponse> => {
    const refreshToken = apiClient.getRefreshToken()
    if (!refreshToken) {
      throw new Error('No refresh token available')
    }
    const response = await apiClient.post<AuthResponse>('/auth/refresh', {
      refresh_token: refreshToken,
    })
    apiClient.setTokens(response.access_token, response.refresh_token)
    return response
  },

  getMe: async (): Promise<User> => {
    return apiClient.get<User>('/auth/me')
  },

  updateMe: async (data: UserUpdateRequest): Promise<User> => {
    return apiClient.patch<User>('/auth/me', data)
  },

  changePassword: async (data: ChangePasswordRequest): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/change-password', data)
  },

  deleteAccount: async (password: string): Promise<{ message: string }> => {
    const response = await apiClient.delete<{ message: string }>('/auth/me', { password })
    apiClient.clearTokens()
    return response
  },

  linkConfig: async (secretStr: string): Promise<void> => {
    await apiClient.post('/auth/link-config', { secret_str: secretStr })
  },
}
