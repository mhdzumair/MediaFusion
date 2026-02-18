/**
 * Instance API - Get information about the current MediaFusion instance
 * and manage API key for private instances.
 */

const API_BASE_URL = '/api/v1'

// Local storage key for API key
const API_KEY_STORAGE_KEY = 'mediafusion_api_key'

export interface NewsletterConfig {
  enabled: boolean // Whether newsletter signup is available
  label: string // Checkbox label text
  default_checked: boolean // Whether the checkbox is checked by default
}

export interface InstanceInfo {
  is_public: boolean
  requires_api_key: boolean
  setup_required: boolean
  addon_name: string
  version: string
  logo_url: string
  branding_svg: string | null // Optional partner/host SVG logo URL
  newsletter: NewsletterConfig
}

export interface SetupCompleteRequest {
  api_password: string
  email: string
  username?: string
  password: string
}

export interface TelegramFeatureConfig {
  enabled: boolean // Whether Telegram streaming is enabled on this instance
  bot_configured: boolean // Whether the Telegram bot is configured
  bot_username: string | null // Bot @username for deep links (without @)
  scraping_enabled: boolean // Whether Telegram scraping is enabled
}

export interface AppConfig {
  addon_name: string
  logo_url: string
  branding_svg: string | null // Optional partner/host SVG logo URL
  host_url: string
  poster_host_url: string | null
  version: string
  description: string
  branding_description: string // Can contain HTML
  is_public_instance: boolean
  contact_email: string | null // Instance operator email (null if not configured)
  disabled_providers: string[]
  disabled_content_types: string[]
  authentication_required: boolean
  torznab_enabled: boolean
  nzb_file_import_enabled: boolean
  telegram: TelegramFeatureConfig
}

/**
 * Get instance information.
 * This endpoint is always accessible (no auth/API key required).
 */
export async function getInstanceInfo(): Promise<InstanceInfo> {
  const response = await fetch(`${API_BASE_URL}/instance/info`)
  if (!response.ok) {
    throw new Error('Failed to fetch instance info')
  }
  return response.json()
}

/**
 * Get full application configuration.
 * This endpoint is always accessible (no auth/API key required).
 */
export async function getAppConfig(): Promise<AppConfig> {
  const response = await fetch(`${API_BASE_URL}/instance/app-config`)
  if (!response.ok) {
    throw new Error('Failed to fetch app config')
  }
  return response.json()
}

/**
 * Get stored API key from localStorage.
 */
export function getStoredApiKey(): string | null {
  if (typeof window === 'undefined') return null
  return localStorage.getItem(API_KEY_STORAGE_KEY)
}

/**
 * Store API key in localStorage.
 */
export function setStoredApiKey(key: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(API_KEY_STORAGE_KEY, key)
}

/**
 * Clear stored API key from localStorage.
 */
export function clearStoredApiKey(): void {
  if (typeof window === 'undefined') return
  localStorage.removeItem(API_KEY_STORAGE_KEY)
}

/**
 * Check if API key is valid by making a test request.
 * Returns true if valid, false otherwise.
 */
export async function validateApiKey(apiKey: string): Promise<boolean> {
  try {
    // Try to access a protected endpoint with the API key
    const response = await fetch(`${API_BASE_URL}/auth/me`, {
      headers: {
        'X-API-Key': apiKey,
      },
    })
    // 401 means invalid key, anything else (including 401 from missing JWT) might be okay
    // We just want to verify the API key itself is accepted
    // A successful response or 401 (no JWT) means the API key was accepted
    return response.status !== 401 || (response.headers.get('content-type')?.includes('application/json') ?? false)
  } catch {
    return false
  }
}

/**
 * Create the first admin account during initial setup.
 * This endpoint is unauthenticated; it is protected by requiring the
 * instance API_PASSWORD in the request body.
 */
export async function completeSetup(data: SetupCompleteRequest): Promise<import('@/types').AuthResponse> {
  const response = await fetch(`${API_BASE_URL}/instance/setup/create-admin`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  })
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Setup failed' }))
    throw new Error(error.detail || 'Setup failed')
  }
  return response.json()
}

export const instanceApi = {
  getInstanceInfo,
  getAppConfig,
  getStoredApiKey,
  setStoredApiKey,
  clearStoredApiKey,
  validateApiKey,
  completeSetup,
}
