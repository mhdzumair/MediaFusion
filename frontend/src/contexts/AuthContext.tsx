import { createContext, useContext, useReducer, useEffect, useCallback, type ReactNode } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { authApi, apiClient, onAuthStateChange } from '@/lib/api'
import { hasPermission, hasMinimumRole } from '@/lib/permissions'
import type { User, UserRole, LoginRequest, RegisterRequest } from '@/types'
import { Permission } from '@/types'

interface AuthState {
  user: User | null
  isLoading: boolean
  isAuthenticated: boolean
}

type AuthAction =
  | { type: 'SET_USER'; payload: User | null }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'LOGOUT' }

const initialState: AuthState = {
  user: null,
  isLoading: true,
  isAuthenticated: false,
}

function authReducer(state: AuthState, action: AuthAction): AuthState {
  switch (action.type) {
    case 'SET_USER':
      return {
        ...state,
        user: action.payload,
        isAuthenticated: !!action.payload,
        isLoading: false,
      }
    case 'SET_LOADING':
      return { ...state, isLoading: action.payload }
    case 'LOGOUT':
      return { ...initialState, isLoading: false }
    default:
      return state
  }
}

interface AuthContextType extends AuthState {
  login: (data: LoginRequest) => Promise<void>
  register: (data: RegisterRequest) => Promise<void>
  logout: () => Promise<void>
  hasPermission: (permission: Permission) => boolean
  hasMinimumRole: (role: UserRole) => boolean
  refetchUser: () => Promise<void>
}

export const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(authReducer, initialState)
  const queryClient = useQueryClient()

  // Query to fetch current user - disabled by default, we control when it runs
  const { refetch: refetchUser } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: authApi.getMe,
    enabled: false, // We'll manually trigger this via refetch()
    retry: false,
    staleTime: 0, // Always fetch fresh data for auth
    gcTime: 0, // Don't cache auth data
  })

  // Handle logout - just update state, let React Router handle navigation
  const handleLogout = useCallback(() => {
    dispatch({ type: 'LOGOUT' })
    // Clear all cached queries to prevent stale data issues
    queryClient.clear()
  }, [queryClient])

  // Listen for auth events from the API client
  useEffect(() => {
    const unsubscribe = onAuthStateChange((event) => {
      if (event === 'logout') {
        handleLogout()
      } else if (event === 'refreshed') {
        // Token was refreshed, refetch user data
        refetchUser()
      }
    })

    return () => {
      unsubscribe()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [handleLogout]) // refetchUser is stable in react-query v5

  // Check for existing token on mount - only run once
  useEffect(() => {
    let isMounted = true

    const verifyAuth = async () => {
      const token = apiClient.getAccessToken()
      if (!token) {
        if (isMounted) {
          dispatch({ type: 'SET_LOADING', payload: false })
        }
        return
      }

      // Clear any stale cached auth data before verifying
      queryClient.removeQueries({ queryKey: ['auth'] })

      try {
        const result = await refetchUser()

        if (!isMounted) return

        // Check for errors first - react-query refetch can return cached data even on error
        if (result.isError || result.error) {
          // Token is invalid/expired, clear it silently (don't emit logout event to avoid loops)
          apiClient.clearTokens(true) // silent = true to prevent logout event during initial verification
          queryClient.removeQueries({ queryKey: ['auth'] })
          dispatch({ type: 'SET_USER', payload: null })
          return
        }

        if (result.data) {
          dispatch({ type: 'SET_USER', payload: result.data })
        } else {
          // No data and no error means something is wrong, clear tokens silently
          apiClient.clearTokens(true) // silent = true
          dispatch({ type: 'SET_USER', payload: null })
        }
      } catch (error) {
        if (!isMounted) return
        // Fetch failed, clear stale tokens silently
        apiClient.clearTokens(true) // silent = true to prevent logout event during initial verification
        queryClient.removeQueries({ queryKey: ['auth'] })
        dispatch({ type: 'SET_USER', payload: null })
      }
    }

    verifyAuth()

    return () => {
      isMounted = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []) // Only run once on mount

  // Login mutation
  const loginMutation = useMutation({
    mutationFn: authApi.login,
    onSuccess: (data) => {
      dispatch({ type: 'SET_USER', payload: data.user })
      queryClient.invalidateQueries({ queryKey: ['auth'] })
    },
  })

  // Register mutation
  const registerMutation = useMutation({
    mutationFn: authApi.register,
    onSuccess: (data) => {
      dispatch({ type: 'SET_USER', payload: data.user })
      queryClient.invalidateQueries({ queryKey: ['auth'] })
    },
  })

  // Logout mutation
  const logoutMutation = useMutation({
    mutationFn: authApi.logout,
    onSuccess: () => {
      handleLogout()
    },
    onError: () => {
      // Even if logout API fails, clear local state
      handleLogout()
    },
  })

  const login = async (data: LoginRequest) => {
    await loginMutation.mutateAsync(data)
  }

  const register = async (data: RegisterRequest) => {
    await registerMutation.mutateAsync(data)
  }

  const logout = async () => {
    await logoutMutation.mutateAsync()
  }

  const checkPermission = (permission: Permission): boolean => {
    if (!state.user) return false
    return hasPermission(state.user.role, permission)
  }

  const checkMinimumRole = (role: UserRole): boolean => {
    if (!state.user) return false
    return hasMinimumRole(state.user.role, role)
  }

  const handleRefetchUser = async () => {
    try {
      const result = await refetchUser()
      if (result.isError || result.error) {
        apiClient.clearTokens()
        dispatch({ type: 'SET_USER', payload: null })
        return
      }
      if (result.data) {
        dispatch({ type: 'SET_USER', payload: result.data })
      }
    } catch {
      apiClient.clearTokens()
      dispatch({ type: 'SET_USER', payload: null })
    }
  }

  return (
    <AuthContext.Provider
      value={{
        ...state,
        login,
        register,
        logout,
        hasPermission: checkPermission,
        hasMinimumRole: checkMinimumRole,
        refetchUser: handleRefetchUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider')
  }
  return context
}
