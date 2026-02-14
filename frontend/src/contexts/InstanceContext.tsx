import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react'
import { useQuery } from '@tanstack/react-query'
import { getInstanceInfo, getStoredApiKey, setStoredApiKey, clearStoredApiKey, apiClient, type InstanceInfo } from '@/lib/api'

interface InstanceContextType {
  instanceInfo: InstanceInfo | null
  isLoading: boolean
  error: Error | null
  apiKey: string | null
  isApiKeyRequired: boolean
  isApiKeySet: boolean
  setApiKey: (key: string) => void
  clearApiKey: () => void
  refetchInstanceInfo: () => Promise<void>
}

export const InstanceContext = createContext<InstanceContextType | null>(null)

export function InstanceProvider({ children }: { children: ReactNode }) {
  const [apiKey, setApiKeyState] = useState<string | null>(() => getStoredApiKey())

  // Query instance info
  const {
    data: instanceInfo,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['instance', 'info'],
    queryFn: getInstanceInfo,
    staleTime: 5 * 60 * 1000, // 5 minutes
    retry: 2,
  })

  // Sync API key with apiClient when it changes
  useEffect(() => {
    if (apiKey) {
      apiClient.setApiKey(apiKey)
    }
  }, [apiKey])

  // Load API key from storage on mount - only if different from initial state
  useEffect(() => {
    const storedKey = getStoredApiKey()
    if (storedKey && storedKey !== apiKey) {
      setApiKeyState(storedKey)
      apiClient.setApiKey(storedKey)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // Only run once on mount

  const handleSetApiKey = useCallback((key: string) => {
    setStoredApiKey(key)
    setApiKeyState(key)
    apiClient.setApiKey(key)
  }, [])

  const handleClearApiKey = useCallback(() => {
    clearStoredApiKey()
    setApiKeyState(null)
    apiClient.clearApiKey()
  }, [])

  const handleRefetch = useCallback(async () => {
    await refetch()
  }, [refetch])

  // Memoize computed values
  const isApiKeyRequired = useMemo(() => instanceInfo?.requires_api_key ?? false, [instanceInfo?.requires_api_key])
  const isApiKeySet = useMemo(() => !!apiKey, [apiKey])

  // Memoize the context value to prevent unnecessary re-renders
  const contextValue = useMemo<InstanceContextType>(() => ({
    instanceInfo: instanceInfo ?? null,
    isLoading,
    error: error as Error | null,
    apiKey,
    isApiKeyRequired,
    isApiKeySet,
    setApiKey: handleSetApiKey,
    clearApiKey: handleClearApiKey,
    refetchInstanceInfo: handleRefetch,
  }), [instanceInfo, isLoading, error, apiKey, isApiKeyRequired, isApiKeySet, handleSetApiKey, handleClearApiKey, handleRefetch])

  return (
    <InstanceContext.Provider value={contextValue}>
      {children}
    </InstanceContext.Provider>
  )
}

export function useInstance() {
  const context = useContext(InstanceContext)
  if (!context) {
    throw new Error('useInstance must be used within an InstanceProvider')
  }
  return context
}

