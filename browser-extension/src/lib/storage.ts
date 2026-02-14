/**
 * Browser Extension Storage Utilities
 * Handles persistent storage using browser.storage.sync API
 */

import type { ExtensionSettings, User, PrefilledData } from './types'

// Default settings
const DEFAULT_SETTINGS: ExtensionSettings = {
  instanceUrl: '',
  authToken: undefined,
  user: undefined,
  defaultContentType: 'movie',
  autoAnalyze: true,
  showNotifications: true,
}

// Storage keys
const STORAGE_KEYS = {
  SETTINGS: 'mediafusion_settings',
  PREFILLED_DATA: 'mediafusion_prefilled_data',
} as const

// Cross-browser storage API
function getStorageApi() {
  // Firefox uses browser.storage, Chrome uses chrome.storage
  if (typeof browser !== 'undefined' && browser.storage) {
    return browser.storage
  }
  if (typeof chrome !== 'undefined' && chrome.storage) {
    return chrome.storage
  }
  throw new Error('No browser storage API available')
}

class Storage {
  private storageApi = getStorageApi()

  // ============================================
  // Settings Management
  // ============================================

  async getSettings(): Promise<ExtensionSettings> {
    try {
      const result = await this.storageApi.sync.get(STORAGE_KEYS.SETTINGS)
      const stored = result[STORAGE_KEYS.SETTINGS]
      
      if (!stored) {
        return { ...DEFAULT_SETTINGS }
      }

      // Merge with defaults to handle any new fields
      return { ...DEFAULT_SETTINGS, ...stored }
    } catch (error) {
      console.error('Failed to get settings:', error)
      return { ...DEFAULT_SETTINGS }
    }
  }

  async saveSettings(settings: Partial<ExtensionSettings>): Promise<void> {
    try {
      const current = await this.getSettings()
      const updated = { ...current, ...settings }
      await this.storageApi.sync.set({ [STORAGE_KEYS.SETTINGS]: updated })
    } catch (error) {
      console.error('Failed to save settings:', error)
      throw error
    }
  }

  async clearSettings(): Promise<void> {
    try {
      await this.storageApi.sync.remove(STORAGE_KEYS.SETTINGS)
    } catch (error) {
      console.error('Failed to clear settings:', error)
      throw error
    }
  }

  // ============================================
  // Auth Management
  // ============================================

  async saveAuth(token: string, user: User, apiKey?: string): Promise<void> {
    await this.saveSettings({
      authToken: token,
      user,
      apiKey,
    })
  }

  async clearAuth(): Promise<void> {
    await this.saveSettings({
      authToken: undefined,
      user: undefined,
      apiKey: undefined,
    })
  }

  async isAuthenticated(): Promise<boolean> {
    const settings = await this.getSettings()
    return !!settings.authToken
  }

  // ============================================
  // Instance URL Management
  // ============================================

  async getInstanceUrl(): Promise<string> {
    const settings = await this.getSettings()
    return settings.instanceUrl
  }

  async setInstanceUrl(url: string): Promise<void> {
    // Normalize URL (remove trailing slash)
    const normalizedUrl = url.replace(/\/+$/, '')
    await this.saveSettings({ instanceUrl: normalizedUrl })
  }

  // ============================================
  // Prefilled Data (for content script communication)
  // ============================================

  async getPrefilledData(): Promise<PrefilledData | null> {
    try {
      const result = await this.storageApi.local.get(STORAGE_KEYS.PREFILLED_DATA)
      return result[STORAGE_KEYS.PREFILLED_DATA] || null
    } catch (error) {
      console.error('Failed to get prefilled data:', error)
      return null
    }
  }

  async setPrefilledData(data: PrefilledData): Promise<void> {
    try {
      await this.storageApi.local.set({ [STORAGE_KEYS.PREFILLED_DATA]: data })
    } catch (error) {
      console.error('Failed to set prefilled data:', error)
      throw error
    }
  }

  async clearPrefilledData(): Promise<void> {
    try {
      await this.storageApi.local.remove(STORAGE_KEYS.PREFILLED_DATA)
    } catch (error) {
      console.error('Failed to clear prefilled data:', error)
    }
  }
}

export const storage = new Storage()

// Type declarations for browser storage APIs
declare const browser: {
  storage: {
    sync: {
      get(keys: string | string[]): Promise<Record<string, unknown>>
      set(items: Record<string, unknown>): Promise<void>
      remove(keys: string | string[]): Promise<void>
    }
    local: {
      get(keys: string | string[]): Promise<Record<string, unknown>>
      set(items: Record<string, unknown>): Promise<void>
      remove(keys: string | string[]): Promise<void>
    }
  }
}
