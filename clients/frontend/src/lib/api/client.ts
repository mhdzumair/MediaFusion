import type { ApiError } from '@/types'

const API_BASE_URL = '/api/v1'
const API_KEY_STORAGE_KEY = 'mediafusion_api_key'

// Event system for auth state changes
type AuthEventListener = (event: 'logout' | 'refreshed') => void
const authListeners: Set<AuthEventListener> = new Set()

export const onAuthStateChange = (listener: AuthEventListener) => {
  authListeners.add(listener)
  return () => authListeners.delete(listener)
}

const emitAuthEvent = (event: 'logout' | 'refreshed') => {
  authListeners.forEach((listener) => listener(event))
}

class ApiClient {
  private accessToken: string | null = null
  private refreshToken: string | null = null
  private apiKey: string | null = null
  private isRefreshing = false
  private refreshPromise: Promise<boolean> | null = null

  constructor() {
    // Try to load tokens from localStorage on init
    if (typeof window !== 'undefined') {
      this.accessToken = localStorage.getItem('access_token')
      this.refreshToken = localStorage.getItem('refresh_token')
      this.apiKey = localStorage.getItem(API_KEY_STORAGE_KEY)
    }
  }

  /**
   * Set the API key for private instance authentication.
   */
  setApiKey(key: string | null) {
    this.apiKey = key
    if (key) {
      localStorage.setItem(API_KEY_STORAGE_KEY, key)
    } else {
      localStorage.removeItem(API_KEY_STORAGE_KEY)
    }
  }

  /**
   * Get the current API key.
   */
  getApiKey(): string | null {
    return this.apiKey
  }

  /**
   * Clear the API key.
   */
  clearApiKey() {
    this.apiKey = null
    localStorage.removeItem(API_KEY_STORAGE_KEY)
  }

  setTokens(accessToken: string | null, refreshToken: string | null = null) {
    this.accessToken = accessToken
    if (refreshToken !== null) {
      this.refreshToken = refreshToken
    }

    if (accessToken) {
      localStorage.setItem('access_token', accessToken)
    } else {
      localStorage.removeItem('access_token')
    }

    if (refreshToken) {
      localStorage.setItem('refresh_token', refreshToken)
    } else if (refreshToken === null && !accessToken) {
      localStorage.removeItem('refresh_token')
    }
  }

  setAccessToken(token: string | null) {
    this.setTokens(token)
  }

  getAccessToken(): string | null {
    return this.accessToken
  }

  getRefreshToken(): string | null {
    return this.refreshToken
  }

  clearTokens(silent = false) {
    // Only emit logout event if there were tokens to clear and not silent
    const hadTokens = this.accessToken !== null || this.refreshToken !== null
    this.accessToken = null
    this.refreshToken = null
    localStorage.removeItem('access_token')
    localStorage.removeItem('refresh_token')
    if (hadTokens && !silent) {
      emitAuthEvent('logout')
    }
  }

  private async refreshAccessToken(): Promise<boolean> {
    // If already refreshing, wait for that promise
    if (this.isRefreshing && this.refreshPromise) {
      return this.refreshPromise
    }

    if (!this.refreshToken) {
      return false
    }

    this.isRefreshing = true
    this.refreshPromise = (async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/auth/refresh`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ refresh_token: this.refreshToken }),
        })

        const data = await response.json()

        if (!response.ok || data.error === true) {
          this.clearTokens()
          return false
        }

        this.setTokens(data.access_token, data.refresh_token || this.refreshToken)
        emitAuthEvent('refreshed')
        return true
      } catch {
        this.clearTokens()
        return false
      } finally {
        this.isRefreshing = false
        this.refreshPromise = null
      }
    })()

    return this.refreshPromise
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit & { useRawUrl?: boolean } = {},
    retry = true,
  ): Promise<T> {
    const { useRawUrl, ...fetchOptions } = options

    const headers: HeadersInit = {
      'Content-Type': 'application/json',
      ...fetchOptions.headers,
    }

    // Add Authorization header for JWT auth
    if (this.accessToken) {
      ;(headers as Record<string, string>)['Authorization'] = `Bearer ${this.accessToken}`
    }

    // Add API key header for private instance authentication
    if (this.apiKey) {
      ;(headers as Record<string, string>)['X-API-Key'] = this.apiKey
    }

    // Use raw URL if specified (for admin routes, etc.), otherwise prepend API_BASE_URL
    const url = useRawUrl ? endpoint : `${API_BASE_URL}${endpoint}`

    const response = await fetch(url, {
      ...fetchOptions,
      headers,
    })

    // Handle Traefik-level 429 (proxy's own rate limiting, before our API)
    if (response.status === 429) {
      throw new ApiRequestError('Too many requests. Please wait and try again.', 429, {
        detail: 'Too many requests. Please wait and try again.',
        status_code: 429,
        error: true,
      })
    }

    // Handle real HTTP 401 (non-wrapped, e.g. from proxy or non-API paths)
    if (response.status === 401) {
      let error: ApiError
      try {
        error = await response.json()
      } catch {
        error = { detail: `HTTP error ${response.status}` }
      }

      const isApiKeyError = error.detail?.toLowerCase().includes('api key') || false
      const isAuthEndpoint = endpoint.includes('/auth/login') || endpoint.includes('/auth/register')

      if (isApiKeyError || isAuthEndpoint) {
        throw new ApiRequestError(error.detail || 'An error occurred', response.status, error)
      }

      if (retry) {
        const refreshed = await this.refreshAccessToken()
        if (refreshed) {
          return this.request<T>(endpoint, options, false)
        }
      }

      this.clearTokens()
      throw new Error('Session expired. Please log in again.')
    }

    // Handle other non-2xx from proxy or non-API paths
    if (!response.ok) {
      let error: ApiError
      try {
        error = await response.json()
      } catch {
        error = { detail: `HTTP error ${response.status}` }
      }

      throw new ApiRequestError(error.detail || 'An error occurred', response.status, error)
    }

    // Handle 204 No Content
    if (response.status === 204) {
      return {} as T
    }

    const data = await response.json()

    // Handle wrapped errors: API returns 200 with { error: true, detail, status_code }
    if (data?.error === true) {
      const statusCode = data.status_code || 500

      // Wrapped 401 — attempt token refresh
      if (statusCode === 401) {
        const isApiKeyError = data.detail?.toLowerCase().includes('api key') || false
        const isAuthEndpoint = endpoint.includes('/auth/login') || endpoint.includes('/auth/register')

        if (isApiKeyError || isAuthEndpoint) {
          throw new ApiRequestError(data.detail || 'An error occurred', statusCode, data)
        }

        if (retry) {
          const refreshed = await this.refreshAccessToken()
          if (refreshed) {
            return this.request<T>(endpoint, options, false)
          }
        }

        this.clearTokens()
        throw new Error('Session expired. Please log in again.')
      }

      throw new ApiRequestError(data.detail || 'An error occurred', statusCode, data)
    }

    return data as T
  }

  async get<T>(endpoint: string): Promise<T> {
    return this.request<T>(endpoint, { method: 'GET' })
  }

  async post<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'POST',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  async put<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PUT',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  async patch<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'PATCH',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  async delete<T>(endpoint: string, data?: unknown): Promise<T> {
    return this.request<T>(endpoint, {
      method: 'DELETE',
      body: data ? JSON.stringify(data) : undefined,
    })
  }

  // Multipart form data for file uploads
  async upload<T>(endpoint: string, formData: FormData, retry = true): Promise<T> {
    const headers: HeadersInit = {}

    if (this.accessToken) {
      headers['Authorization'] = `Bearer ${this.accessToken}`
    }

    // Add API key header for private instance authentication
    if (this.apiKey) {
      headers['X-API-Key'] = this.apiKey
    }

    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: 'POST',
      headers,
      body: formData,
    })

    // Handle Traefik-level 429
    if (response.status === 429) {
      throw new ApiRequestError('Too many requests. Please wait and try again.', 429, {
        detail: 'Too many requests. Please wait and try again.',
        status_code: 429,
        error: true,
      })
    }

    // Handle real HTTP 401 - try to refresh token
    if (response.status === 401 && retry) {
      const refreshed = await this.refreshAccessToken()
      if (refreshed) {
        return this.upload<T>(endpoint, formData, false)
      }
      this.clearTokens()
      throw new Error('Session expired. Please log in again.')
    }

    if (!response.ok) {
      let error: ApiError
      try {
        error = await response.json()
      } catch {
        error = { detail: `HTTP error ${response.status}` }
      }
      throw new ApiRequestError(error.detail || 'An error occurred', response.status, error)
    }

    const data = await response.json()

    // Handle wrapped errors in upload responses
    if (data?.error === true) {
      const statusCode = data.status_code || 500

      if (statusCode === 401 && retry) {
        const refreshed = await this.refreshAccessToken()
        if (refreshed) {
          return this.upload<T>(endpoint, formData, false)
        }
        this.clearTokens()
        throw new Error('Session expired. Please log in again.')
      }

      throw new ApiRequestError(data.detail || 'An error occurred', statusCode, data)
    }

    return data as T
  }
}

// Custom error class for better error handling
export class ApiRequestError extends Error {
  status: number
  data: ApiError

  constructor(message: string, status: number, data: ApiError) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.data = data
  }
}

export const apiClient = new ApiClient()
