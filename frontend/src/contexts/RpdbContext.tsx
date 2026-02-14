import { createContext, useContext, type ReactNode } from 'react'
import { useRpdbApiKey } from '@/hooks'
import { useAuth } from './AuthContext'

interface RpdbContextType {
  rpdbApiKey: string | null
  isLoading: boolean
}

// Default value for when context is not available
const defaultRpdbContext: RpdbContextType = {
  rpdbApiKey: null,
  isLoading: false,
}

const RpdbContext = createContext<RpdbContextType>(defaultRpdbContext)

export function RpdbProvider({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth()

  // Only fetch RPDB key when authenticated
  const { data, isLoading } = useRpdbApiKey(isAuthenticated)

  const value: RpdbContextType = {
    rpdbApiKey: data?.rpdb_api_key ?? null,
    isLoading: isAuthenticated ? isLoading : false,
  }

  return <RpdbContext.Provider value={value}>{children}</RpdbContext.Provider>
}

/**
 * Hook to access RPDB API key context
 * Returns default values (null key, not loading) if used outside provider
 */
export function useRpdb(): RpdbContextType {
  return useContext(RpdbContext)
}

export { RpdbContext }
