/**
 * MediaFusion Browser Extension API Client
 * Handles all API calls to the MediaFusion instance
 */

import type {
  TorrentAnalyzeResponse,
  ImportResponse,
  TorrentImportRequest,
  LoginRequest,
  LoginResponse,
  CatalogsResponse,
} from './types'
import { storage } from './storage'

class ApiClient {
  private async getAuthHeaders(): Promise<Record<string, string>> {
    const settings = await storage.getSettings()
    const headers: Record<string, string> = {
      'Accept': 'application/json',
    }
    
    if (settings.authToken) {
      headers['Authorization'] = `Bearer ${settings.authToken}`
    }
    
    // Include API key for private instances
    if (settings.apiKey) {
      headers['X-API-Key'] = settings.apiKey
    }
    
    return headers
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const settings = await storage.getSettings()
    
    if (!settings.instanceUrl) {
      throw new Error('MediaFusion instance URL not configured')
    }

    const baseUrl = settings.instanceUrl.replace(/\/$/, '')
    const url = `${baseUrl}${endpoint}`
    
    const headers = await this.getAuthHeaders()
    
    const response = await fetch(url, {
      ...options,
      headers: {
        ...headers,
        ...options.headers,
      },
    })

    if (!response.ok) {
      if (response.status === 401) {
        // Token expired or invalid - clear auth
        await storage.clearAuth()
        throw new Error('Authentication expired. Please log in again.')
      }
      
      const errorData = await response.json().catch(() => ({}))
      // Handle FastAPI validation errors (array of objects) or simple string errors
      let errorMessage = `Request failed: ${response.status}`
      if (errorData.detail) {
        if (Array.isArray(errorData.detail)) {
          // FastAPI validation errors
          errorMessage = errorData.detail
            .map((err: { msg?: string; loc?: string[] }) => {
              const field = err.loc?.slice(-1)[0] || 'unknown'
              return `${field}: ${err.msg || 'invalid'}`
            })
            .join(', ')
        } else if (typeof errorData.detail === 'string') {
          errorMessage = errorData.detail
        }
      }
      throw new Error(errorMessage)
    }

    return response.json()
  }

  // ============================================
  // Auth Endpoints
  // ============================================

  async login(credentials: LoginRequest): Promise<LoginResponse> {
    const settings = await storage.getSettings()
    
    if (!settings.instanceUrl) {
      throw new Error('MediaFusion instance URL not configured')
    }

    const baseUrl = settings.instanceUrl.replace(/\/$/, '')
    
    // Use form data for login endpoint
    const formData = new URLSearchParams()
    formData.append('username', credentials.email)
    formData.append('password', credentials.password)

    const response = await fetch(`${baseUrl}/api/v1/auth/login`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded',
      },
      body: formData,
    })

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || 'Login failed')
    }

    return response.json()
  }

  async testConnection(): Promise<boolean> {
    try {
      const settings = await storage.getSettings()
      
      if (!settings.instanceUrl) {
        return false
      }

      const baseUrl = settings.instanceUrl.replace(/\/$/, '')
      const response = await fetch(`${baseUrl}/health`, {
        method: 'GET',
        headers: await this.getAuthHeaders(),
      })

      return response.ok
    } catch {
      return false
    }
  }

  async getCurrentUser(): Promise<{ id: number; email: string; display_name: string; role: string } | null> {
    try {
      return await this.request('/api/v1/auth/me')
    } catch {
      return null
    }
  }

  // ============================================
  // Import Endpoints
  // ============================================

  async analyzeMagnet(
    magnetLink: string,
    metaType: 'movie' | 'series' | 'sports'
  ): Promise<TorrentAnalyzeResponse> {
    return this.request('/api/v1/import/magnet/analyze', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        magnet_link: magnetLink,
        meta_type: metaType,
      }),
    })
  }

  async analyzeTorrent(
    file: File,
    metaType: 'movie' | 'series' | 'sports'
  ): Promise<TorrentAnalyzeResponse> {
    const formData = new FormData()
    formData.append('torrent_file', file)
    formData.append('meta_type', metaType)

    const settings = await storage.getSettings()
    const baseUrl = settings.instanceUrl?.replace(/\/$/, '') || ''
    
    if (!baseUrl) {
      throw new Error('MediaFusion instance URL not configured')
    }

    const headers = await this.getAuthHeaders()
    // Don't set Content-Type for FormData - browser will set it with boundary
    delete (headers as Record<string, string>)['Content-Type']

    const response = await fetch(`${baseUrl}/api/v1/import/torrent/analyze`, {
      method: 'POST',
      headers,
      body: formData,
    })

    if (!response.ok) {
      if (response.status === 401) {
        await storage.clearAuth()
        throw new Error('Authentication expired. Please log in again.')
      }
      const errorData = await response.json().catch(() => ({}))
      throw new Error(errorData.detail || `Request failed: ${response.status}`)
    }

    return response.json()
  }

  async importMagnet(request: TorrentImportRequest & { magnet_link: string }): Promise<ImportResponse> {
    const formData = new FormData()
    formData.append('magnet_link', request.magnet_link)
    formData.append('meta_type', request.meta_type)
    if (request.meta_id) formData.append('meta_id', request.meta_id)
    if (request.title) formData.append('title', request.title)
    if (request.poster) formData.append('poster', request.poster)
    if (request.background) formData.append('background', request.background)
    if (request.logo) formData.append('logo', request.logo)
    if (request.resolution) formData.append('resolution', request.resolution)
    if (request.quality) formData.append('quality', request.quality)
    if (request.codec) formData.append('codec', request.codec)
    if (request.audio) formData.append('audio', request.audio)
    if (request.hdr) formData.append('hdr', request.hdr)
    if (request.languages) formData.append('languages', request.languages)
    if (request.catalogs) formData.append('catalogs', request.catalogs)
    if (request.file_data) formData.append('file_data', request.file_data)
    if (request.force_import) formData.append('force_import', 'true')
    if (request.is_anonymous) formData.append('is_anonymous', 'true')
    if (request.sports_category) formData.append('sports_category', request.sports_category)
    if (request.episode_name_parser) formData.append('episode_name_parser', request.episode_name_parser)

    const settings = await storage.getSettings()
    const baseUrl = settings.instanceUrl?.replace(/\/$/, '') || ''
    
    if (!baseUrl) {
      throw new Error('MediaFusion instance URL not configured')
    }

    const headers = await this.getAuthHeaders()
    // Don't set Content-Type for FormData - browser will set it with boundary
    delete (headers as Record<string, string>)['Content-Type']

    const response = await fetch(`${baseUrl}/api/v1/import/magnet`, {
      method: 'POST',
      headers,
      body: formData,
    })

    if (!response.ok) {
      if (response.status === 401) {
        await storage.clearAuth()
        throw new Error('Authentication expired. Please log in again.')
      }
      const errorData = await response.json().catch(() => ({}))
      // Handle FastAPI validation errors (array of objects) or simple string errors
      let errorMessage = `Request failed: ${response.status}`
      if (errorData.detail) {
        if (Array.isArray(errorData.detail)) {
          errorMessage = errorData.detail
            .map((err: { msg?: string; loc?: string[] }) => {
              const field = err.loc?.slice(-1)[0] || 'unknown'
              return `${field}: ${err.msg || 'invalid'}`
            })
            .join(', ')
        } else if (typeof errorData.detail === 'string') {
          errorMessage = errorData.detail
        }
      }
      throw new Error(errorMessage)
    }

    return response.json()
  }

  async importTorrent(
    file: File,
    request: Omit<TorrentImportRequest, 'magnet_link'>
  ): Promise<ImportResponse> {
    const formData = new FormData()
    formData.append('torrent_file', file)
    formData.append('meta_type', request.meta_type)
    if (request.meta_id) formData.append('meta_id', request.meta_id)
    if (request.title) formData.append('title', request.title)
    if (request.poster) formData.append('poster', request.poster)
    if (request.background) formData.append('background', request.background)
    if (request.logo) formData.append('logo', request.logo)
    if (request.resolution) formData.append('resolution', request.resolution)
    if (request.quality) formData.append('quality', request.quality)
    if (request.codec) formData.append('codec', request.codec)
    if (request.audio) formData.append('audio', request.audio)
    if (request.hdr) formData.append('hdr', request.hdr)
    if (request.languages) formData.append('languages', request.languages)
    if (request.catalogs) formData.append('catalogs', request.catalogs)
    if (request.file_data) formData.append('file_data', request.file_data)
    if (request.force_import) formData.append('force_import', 'true')
    if (request.is_anonymous) formData.append('is_anonymous', 'true')
    if (request.sports_category) formData.append('sports_category', request.sports_category)
    if (request.episode_name_parser) formData.append('episode_name_parser', request.episode_name_parser)

    const settings = await storage.getSettings()
    const baseUrl = settings.instanceUrl?.replace(/\/$/, '') || ''
    
    if (!baseUrl) {
      throw new Error('MediaFusion instance URL not configured')
    }

    const headers = await this.getAuthHeaders()
    delete (headers as Record<string, string>)['Content-Type']

    const response = await fetch(`${baseUrl}/api/v1/import/torrent`, {
      method: 'POST',
      headers,
      body: formData,
    })

    if (!response.ok) {
      if (response.status === 401) {
        await storage.clearAuth()
        throw new Error('Authentication expired. Please log in again.')
      }
      const errorData = await response.json().catch(() => ({}))
      // Handle FastAPI validation errors (array of objects) or simple string errors
      let errorMessage = `Request failed: ${response.status}`
      if (errorData.detail) {
        if (Array.isArray(errorData.detail)) {
          errorMessage = errorData.detail
            .map((err: { msg?: string; loc?: string[] }) => {
              const field = err.loc?.slice(-1)[0] || 'unknown'
              return `${field}: ${err.msg || 'invalid'}`
            })
            .join(', ')
        } else if (typeof errorData.detail === 'string') {
          errorMessage = errorData.detail
        }
      }
      throw new Error(errorMessage)
    }

    return response.json()
  }

  // ============================================
  // Catalog Data
  // ============================================

  async getCatalogs(): Promise<CatalogsResponse> {
    return this.request('/api/v1/import/catalogs')
  }
}

export const api = new ApiClient()
