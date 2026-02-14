import { createContext, useContext, useEffect, useState, type ReactNode } from 'react'

type Theme = 'dark' | 'light' | 'system'

// Available color schemes
export type ColorScheme = 'mediafusion' | 'cinematic' | 'ocean' | 'forest' | 'rose' | 'purple' | 'sunset' | 'youtube'

export interface ColorSchemeConfig {
  id: ColorScheme
  name: string
  description: string
  preview: {
    primary: string
    secondary: string
    accent: string
  }
}

export const colorSchemes: ColorSchemeConfig[] = [
  {
    id: 'mediafusion',
    name: 'MediaFusion',
    description: 'Signature purple-to-orange gradient inspired by the logo',
    preview: {
      primary: '#7c3aed',
      secondary: '#f97316',
      accent: '#a855f7',
    },
  },
  {
    id: 'cinematic',
    name: 'Cinematic Gold',
    description: 'Premium film-inspired theme with amber/gold accents',
    preview: {
      primary: '#d4a853',
      secondary: '#b8942e',
      accent: '#f5d78e',
    },
  },
  {
    id: 'ocean',
    name: 'Ocean Depths',
    description: 'Cool blues and teals inspired by the deep sea',
    preview: {
      primary: '#0ea5e9',
      secondary: '#0284c7',
      accent: '#7dd3fc',
    },
  },
  {
    id: 'forest',
    name: 'Forest Emerald',
    description: 'Natural greens with earthy undertones',
    preview: {
      primary: '#10b981',
      secondary: '#059669',
      accent: '#6ee7b7',
    },
  },
  {
    id: 'rose',
    name: 'Rose Quartz',
    description: 'Soft pinks and warm magentas',
    preview: {
      primary: '#f43f5e',
      secondary: '#ec4899',
      accent: '#fda4af',
    },
  },
  {
    id: 'purple',
    name: 'Nebula Purple',
    description: 'Classic purple and violet cosmic theme',
    preview: {
      primary: '#8b5cf6',
      secondary: '#a855f7',
      accent: '#c4b5fd',
    },
  },
  {
    id: 'sunset',
    name: 'Sunset Blaze',
    description: 'Warm oranges and deep reds',
    preview: {
      primary: '#f97316',
      secondary: '#ef4444',
      accent: '#fdba74',
    },
  },
  {
    id: 'youtube',
    name: 'YouTube Red',
    description: 'Classic YouTube-inspired red and white theme',
    preview: {
      primary: '#ff0000',
      secondary: '#cc0000',
      accent: '#ff4e45',
    },
  },
]

interface ThemeContextType {
  theme: Theme
  setTheme: (theme: Theme) => void
  resolvedTheme: 'dark' | 'light'
  colorScheme: ColorScheme
  setColorScheme: (scheme: ColorScheme) => void
}

const ThemeContext = createContext<ThemeContextType | null>(null)

const THEME_STORAGE_KEY = 'mediafusion-theme'
const COLOR_SCHEME_STORAGE_KEY = 'mediafusion-color-scheme'

function getSystemTheme(): 'dark' | 'light' {
  if (typeof window === 'undefined') return 'dark'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof window === 'undefined') return 'system'
    const stored = localStorage.getItem(THEME_STORAGE_KEY) as Theme | null
    return stored || 'system'
  })
  
  const [colorScheme, setColorSchemeState] = useState<ColorScheme>(() => {
    if (typeof window === 'undefined') return 'mediafusion'
    const stored = localStorage.getItem(COLOR_SCHEME_STORAGE_KEY) as ColorScheme | null
    return stored || 'mediafusion'
  })
  
  const [resolvedTheme, setResolvedTheme] = useState<'dark' | 'light'>(() => {
    if (theme === 'system') return getSystemTheme()
    return theme
  })

  // Update resolved theme when theme changes
  useEffect(() => {
    const resolved = theme === 'system' ? getSystemTheme() : theme
    setResolvedTheme(resolved)
  }, [theme])

  // Listen for system theme changes
  useEffect(() => {
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)')
    
    const handleChange = () => {
      if (theme === 'system') {
        setResolvedTheme(getSystemTheme())
      }
    }

    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [theme])

  // Apply theme class and color scheme to document
  useEffect(() => {
    const root = window.document.documentElement
    
    // Remove old theme classes
    root.classList.remove('light', 'dark')
    root.classList.add(resolvedTheme)
    
    // Remove old color scheme classes
    colorSchemes.forEach(scheme => {
      root.classList.remove(`scheme-${scheme.id}`)
    })
    
    // Add new color scheme class
    root.classList.add(`scheme-${colorScheme}`)
  }, [resolvedTheme, colorScheme])

  const setTheme = (newTheme: Theme) => {
    setThemeState(newTheme)
    localStorage.setItem(THEME_STORAGE_KEY, newTheme)
  }

  const setColorScheme = (newScheme: ColorScheme) => {
    setColorSchemeState(newScheme)
    localStorage.setItem(COLOR_SCHEME_STORAGE_KEY, newScheme)
  }

  return (
    <ThemeContext.Provider value={{ theme, setTheme, resolvedTheme, colorScheme, setColorScheme }}>
      {children}
    </ThemeContext.Provider>
  )
}

export function useTheme() {
  const context = useContext(ThemeContext)
  if (!context) {
    throw new Error('useTheme must be used within a ThemeProvider')
  }
  return context
}
