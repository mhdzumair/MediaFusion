/**
 * Indexer Settings API
 *
 * Provides test endpoints for Prowlarr, Jackett, and Torznab connections.
 * The actual indexer configuration is stored as part of the user profile.
 */

import { apiClient } from './client'

// Types
export interface IndexerInstanceConfig {
  enabled: boolean
  url: string | null
  api_key: string | null
  use_global: boolean
}

export interface TorznabEndpoint {
  id?: string
  name: string
  url: string
  headers?: Record<string, string> | null
  enabled: boolean
  categories: number[]
  priority: number
}

export interface NewznabIndexer {
  name: string
  url: string
  api_key: string
  enabled: boolean
  categories: number[]
}

export interface IndexerConfig {
  prowlarr?: IndexerInstanceConfig | null
  jackett?: IndexerInstanceConfig | null
  torznab_endpoints?: TorznabEndpoint[]
}

export interface IndexerHealth {
  name: string
  id: string | number | null
  enabled: boolean
  status: 'healthy' | 'unhealthy' | 'warning' | 'disabled' | 'unknown'
  error_message: string | null
  priority: number | null
}

export interface ConnectionTestResult {
  success: boolean
  message: string
  indexer_count: number | null
  indexer_names: string[] | null
  indexers: IndexerHealth[] | null
}

export interface GlobalIndexerStatus {
  prowlarr_available: boolean
  jackett_available: boolean
}

// API Functions

/**
 * Get global indexer availability status
 */
export async function getGlobalIndexerStatus(): Promise<GlobalIndexerStatus> {
  return apiClient.get<GlobalIndexerStatus>('/profile/indexers/global-status')
}

/**
 * Test Prowlarr connection
 */
export async function testProwlarrConnection(config: IndexerInstanceConfig): Promise<ConnectionTestResult> {
  return apiClient.post<ConnectionTestResult>('/profile/indexers/prowlarr/test', config)
}

/**
 * Test Jackett connection
 */
export async function testJackettConnection(config: IndexerInstanceConfig): Promise<ConnectionTestResult> {
  return apiClient.post<ConnectionTestResult>('/profile/indexers/jackett/test', config)
}

/**
 * Test a Torznab endpoint configuration
 */
export async function testTorznabEndpoint(endpoint: Omit<TorznabEndpoint, 'id'>): Promise<ConnectionTestResult> {
  return apiClient.post<ConnectionTestResult>('/profile/indexers/torznab/test', endpoint)
}

/**
 * Test a Newznab indexer configuration
 */
export async function testNewznabIndexer(indexer: NewznabIndexer): Promise<ConnectionTestResult> {
  return apiClient.post<ConnectionTestResult>('/profile/indexers/newznab/test', indexer)
}
