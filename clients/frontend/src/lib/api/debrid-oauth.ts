/**
 * Debrid OAuth API - handles OAuth flows for streaming providers
 */

// Device code response from providers
export interface DeviceCodeResponse {
  device_code: string
  user_code?: string
  verification_url?: string
  verification_uri?: string // Some providers use this instead
  expires_in: number
  interval: number
  direct_verification_url?: string
}

// Authorization response
export interface AuthorizeResponse {
  token?: string
  error?: string
  error_code?: number
  message?: string
}

export type OAuthMode = 'device_code' | 'redirect'

interface OAuthProviderEndpoints {
  mode: OAuthMode
  authorize: string
  getDeviceCode?: string
}

// OAuth endpoints for different providers
// Note: AllDebrid does NOT support OAuth - it only uses API key
const OAUTH_ENDPOINTS: Record<string, OAuthProviderEndpoints> = {
  realdebrid: {
    mode: 'device_code',
    getDeviceCode: '/streaming_provider/realdebrid/get-device-code',
    authorize: '/streaming_provider/realdebrid/authorize',
  },
  premiumize: {
    mode: 'redirect',
    authorize: '/streaming_provider/premiumize/authorize',
  },
  debridlink: {
    mode: 'device_code',
    getDeviceCode: '/streaming_provider/debridlink/get-device-code',
    authorize: '/streaming_provider/debridlink/authorize',
  },
  seedr: {
    mode: 'device_code',
    getDeviceCode: '/streaming_provider/seedr/get-device-code',
    authorize: '/streaming_provider/seedr/authorize',
  },
}

/**
 * Get device code for OAuth flow
 */
export async function getDeviceCode(provider: string): Promise<DeviceCodeResponse> {
  const endpoints = OAUTH_ENDPOINTS[provider]
  if (!endpoints) {
    throw new Error(`OAuth not supported for provider: ${provider}`)
  }
  if (endpoints.mode !== 'device_code' || !endpoints.getDeviceCode) {
    throw new Error(`${provider} does not use device-code OAuth`)
  }

  const response = await fetch(endpoints.getDeviceCode)
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Failed to get device code' }))
    throw new Error(error.detail || error.message || 'Failed to get device code')
  }

  return response.json()
}

/**
 * Authorize with device code and get token
 */
export async function authorizeWithDeviceCode(provider: string, deviceCode: string): Promise<AuthorizeResponse> {
  const endpoints = OAUTH_ENDPOINTS[provider]
  if (!endpoints) {
    throw new Error(`OAuth not supported for provider: ${provider}`)
  }
  if (endpoints.mode !== 'device_code') {
    throw new Error(`${provider} does not support device-code polling`)
  }

  const response = await fetch(endpoints.authorize, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ device_code: deviceCode }),
  })

  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: 'Authorization failed' }))
    throw new Error(error.detail || error.message || 'Authorization failed')
  }

  return response.json()
}

/**
 * Check if OAuth is supported for a provider
 */
export function isOAuthSupported(provider: string): boolean {
  return provider in OAUTH_ENDPOINTS
}

/**
 * Get providers that support OAuth
 */
export function getOAuthProviders(): string[] {
  return Object.keys(OAUTH_ENDPOINTS)
}

/**
 * Get OAuth mode for a provider
 */
export function getOAuthMode(provider: string): OAuthMode | null {
  return OAUTH_ENDPOINTS[provider]?.mode ?? null
}

/**
 * Get provider authorization URL endpoint
 */
export function getOAuthAuthorizationUrl(provider: string): string | null {
  return OAUTH_ENDPOINTS[provider]?.authorize ?? null
}
