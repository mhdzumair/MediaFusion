import { apiClient } from './client'
import type { User, UserRole } from '@/types'

export type UserSortField = 'user' | 'role' | 'contribution' | 'status' | 'joined'
export type UserSortOrder = 'asc' | 'desc'

export interface UserListParams {
  page?: number
  per_page?: number
  role?: UserRole
  search?: string
  sort_by?: UserSortField
  sort_order?: UserSortOrder
}

export interface UserListResponse {
  items: User[]
  total: number
  page: number
  per_page: number
  pages: number
}

export interface UserUpdateRequest {
  username?: string
  is_active?: boolean
  is_verified?: boolean
  uploads_restricted?: boolean
}

export interface RoleUpdateRequest {
  role: UserRole
}

export interface SendUploadWarningRequest {
  reason?: string
}

export const usersApi = {
  /**
   * List all users (Admin only)
   */
  list: async (params: UserListParams = {}): Promise<UserListResponse> => {
    const searchParams = new URLSearchParams()
    if (params.page) searchParams.append('page', params.page.toString())
    if (params.per_page) searchParams.append('per_page', params.per_page.toString())
    if (params.role) searchParams.append('role', params.role)
    if (params.search) searchParams.append('search', params.search)
    if (params.sort_by) searchParams.append('sort_by', params.sort_by)
    if (params.sort_order) searchParams.append('sort_order', params.sort_order)

    const query = searchParams.toString()
    return apiClient.get<UserListResponse>(`/users${query ? `?${query}` : ''}`)
  },

  /**
   * Get a specific user by ID (Admin only)
   */
  get: async (userId: string): Promise<User> => {
    return apiClient.get<User>(`/users/${userId}`)
  },

  /**
   * Update a user's information (Admin only)
   */
  update: async (userId: string, data: UserUpdateRequest): Promise<User> => {
    return apiClient.patch<User>(`/users/${userId}`, data)
  },

  /**
   * Update a user's role (Admin only)
   */
  updateRole: async (userId: string, data: RoleUpdateRequest): Promise<User> => {
    return apiClient.patch<User>(`/users/${userId}/role`, data)
  },

  /**
   * Send a manual upload warning email (Admin only)
   */
  sendUploadWarning: async (userId: string, data: SendUploadWarningRequest = {}): Promise<{ message: string }> => {
    return apiClient.post<{ message: string }>(`/users/${userId}/send-upload-warning`, data)
  },

  /**
   * Delete a user (Admin only)
   */
  delete: async (userId: string): Promise<{ message: string }> => {
    return apiClient.delete<{ message: string }>(`/users/${userId}`)
  },
}
