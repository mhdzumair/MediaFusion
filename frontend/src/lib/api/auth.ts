import { apiClient } from './client'
import type { AuthResponse, LoginRequest, RegisterRequest, RegisterResponse, User } from '@/types'

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

  register: async (data: RegisterRequest): Promise<AuthResponse | RegisterResponse> => {
    const response = await apiClient.post<AuthResponse | RegisterResponse>('/auth/register', data)
    // Only set tokens if this is a full AuthResponse (no verification required)
    if ('access_token' in response) {
      apiClient.setTokens(response.access_token, response.refresh_token)
    }
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

  verifyEmail: async (token: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/verify-email', { token })
  },

  resendVerification: async (email: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/resend-verification', { email })
  },

  forgotPassword: async (email: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/forgot-password', { email })
  },

  resetPassword: async (token: string, newPassword: string): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>('/auth/reset-password', { token, new_password: newPassword })
  },
}
