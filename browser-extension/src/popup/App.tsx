import { useState, useEffect } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ImportTab } from '@/components/ImportTab'
import { SettingsTab } from '@/components/SettingsTab'
import { BulkUploadTab } from '@/components/BulkUploadTab'
import { storage } from '@/lib/storage'
import type { ExtensionSettings, PrefilledData } from '@/lib/types'
import { Upload, Settings, LogOut, AlertCircle, Layers } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'

// Type for bulk upload data stored in extension storage
interface BulkUploadData {
  torrents: Array<{
    magnetLink: string
    title: string
    size?: string
    seeders?: number
  }>
  sourceUrl: string
  pageTitle: string
  timestamp: number
}

function App() {
  const [settings, setSettings] = useState<ExtensionSettings | null>(null)
  const [prefilledData, setPrefilledData] = useState<PrefilledData | null>(null)
  const [bulkData, setBulkData] = useState<BulkUploadData | null>(null)
  const [isBulkMode, setIsBulkMode] = useState(false)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState('import')

  useEffect(() => {
    loadData()
    
    // Set up storage change listener to detect auth changes from content script
    const handleStorageChange = () => {
      loadData()
    }
    
    // Listen for storage changes (when auth is saved from website)
    if (typeof browser !== 'undefined' && browser.storage) {
      browser.storage.onChanged.addListener(handleStorageChange)
    } else if (typeof chrome !== 'undefined' && chrome.storage) {
      chrome.storage.onChanged.addListener(handleStorageChange)
    }
    
    return () => {
      if (typeof browser !== 'undefined' && browser.storage) {
        browser.storage.onChanged.removeListener(handleStorageChange)
      } else if (typeof chrome !== 'undefined' && chrome.storage) {
        chrome.storage.onChanged.removeListener(handleStorageChange)
      }
    }
  }, [])

  async function loadData() {
    try {
      // Check URL parameters for bulk mode
      const urlParams = new URLSearchParams(window.location.search)
      const isBulk = urlParams.get('bulk') === 'true'
      setIsBulkMode(isBulk)

      const [savedSettings, prefilled] = await Promise.all([
        storage.getSettings(),
        storage.getPrefilledData(),
      ])
      setSettings(savedSettings)
      setPrefilledData(prefilled)
      
      // Clear prefilled data after loading
      if (prefilled) {
        await storage.clearPrefilledData()
      }

      // Load bulk data if in bulk mode
      if (isBulk) {
        const bulkUploadData = await loadBulkData()
        if (bulkUploadData) {
          setBulkData(bulkUploadData)
          setActiveTab('bulk')
        }
      }
      
      // If not configured, show settings tab
      if (!savedSettings.instanceUrl) {
        setActiveTab('settings')
      }
    } catch (error) {
      console.error('Failed to load settings:', error)
    } finally {
      setLoading(false)
    }
  }

  async function loadBulkData(): Promise<BulkUploadData | null> {
    return new Promise((resolve) => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const tryStorage = (storageApi: any) => {
        storageApi.get(['bulkUploadData'], (result: { bulkUploadData?: BulkUploadData }) => {
          if (result.bulkUploadData) {
            // Clear the data after reading
            storageApi.remove(['bulkUploadData'])
            resolve(result.bulkUploadData)
          } else {
            resolve(null)
          }
        })
      }

      // Try session storage first, then local storage
      if (typeof chrome !== 'undefined' && chrome.storage) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const chromeStorage = chrome.storage as any
        if (chromeStorage.session) {
          tryStorage(chromeStorage.session)
        } else if (chromeStorage.local) {
          tryStorage(chromeStorage.local)
        } else {
          resolve(null)
        }
      } else if (typeof browser !== 'undefined' && browser.storage) {
        if (browser.storage.session) {
          tryStorage(browser.storage.session)
        } else if (browser.storage.local) {
          tryStorage(browser.storage.local)
        } else {
          resolve(null)
        }
      } else {
        resolve(null)
      }
    })
  }

  async function handleLogout() {
    await storage.clearAuth()
    const updated = await storage.getSettings()
    setSettings(updated)
  }

  async function handleSettingsUpdate(newSettings: Partial<ExtensionSettings>) {
    await storage.saveSettings(newSettings)
    const updated = await storage.getSettings()
    setSettings(updated)
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[500px] min-w-[380px] bg-background">
        <div className="animate-pulse text-muted-foreground">Loading...</div>
      </div>
    )
  }

  const isConfigured = !!settings?.instanceUrl
  const isAuthenticated = !!settings?.authToken

  // If not configured, show settings only
  if (!isConfigured) {
    return (
      <div className="min-h-[500px] min-w-[380px] bg-background p-4">
        <Header />
        <SettingsTab 
          settings={settings!} 
          onUpdate={handleSettingsUpdate}
          onConfigured={() => setActiveTab('settings')}
          onLogout={handleLogout}
        />
      </div>
    )
  }

  return (
    <div className="min-h-[500px] min-w-[380px] bg-background">
      <div className="p-4 pb-2">
        <Header 
          user={settings?.user?.display_name} 
          onLogout={isAuthenticated ? handleLogout : undefined}
        />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <div className="px-4">
          <TabsList className="w-full">
            {isBulkMode && bulkData ? (
              <>
                <TabsTrigger value="bulk" className="flex-1 gap-1">
                  <Layers className="h-4 w-4" />
                  Bulk ({bulkData.torrents.length})
                </TabsTrigger>
                <TabsTrigger value="settings" className="flex-1 gap-1">
                  <Settings className="h-4 w-4" />
                  Settings
                </TabsTrigger>
              </>
            ) : (
              <>
                <TabsTrigger value="import" className="flex-1 gap-1">
                  <Upload className="h-4 w-4" />
                  Import
                </TabsTrigger>
                <TabsTrigger value="settings" className="flex-1 gap-1">
                  <Settings className="h-4 w-4" />
                  Settings
                </TabsTrigger>
              </>
            )}
          </TabsList>
        </div>

        {isBulkMode && bulkData && (
          <TabsContent value="bulk" className="mt-0 p-4 pt-2">
            {!isAuthenticated ? (
              <div className="space-y-4">
                <Alert>
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>
                    You need to login to upload torrents. Go to Settings and click "Login via Website".
                  </AlertDescription>
                </Alert>
                <Button 
                  onClick={() => setActiveTab('settings')} 
                  className="w-full"
                  variant="outline"
                >
                  Go to Settings
                </Button>
              </div>
            ) : (
              <BulkUploadTab 
                bulkData={bulkData}
              />
            )}
          </TabsContent>
        )}

        <TabsContent value="import" className="mt-0 p-4 pt-2">
          {!isAuthenticated ? (
            <div className="space-y-4">
              <Alert>
                <AlertCircle className="h-4 w-4" />
                <AlertDescription>
                  You need to login to upload torrents. Go to Settings and click "Login via Website".
                </AlertDescription>
              </Alert>
              <Button 
                onClick={() => setActiveTab('settings')} 
                className="w-full"
                variant="outline"
              >
                Go to Settings
              </Button>
            </div>
          ) : (
            <ImportTab 
              settings={settings!}
              prefilledData={prefilledData}
            />
          )}
        </TabsContent>

        <TabsContent value="settings" className="mt-0 p-4 pt-2">
          <SettingsTab 
            settings={settings!} 
            onUpdate={handleSettingsUpdate}
            onLogout={handleLogout}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}

// Type declarations for browser storage APIs
declare const browser: {
  storage: {
    onChanged: {
      addListener(callback: () => void): void
      removeListener(callback: () => void): void
    }
    session?: {
      get(keys: string[], callback: (result: Record<string, unknown>) => void): void
      remove(keys: string[]): void
    }
    local: {
      get(keys: string[], callback: (result: Record<string, unknown>) => void): void
      remove(keys: string[]): void
    }
  }
}

interface HeaderProps {
  user?: string
  onLogout?: () => void
}

function Header({ user, onLogout }: HeaderProps) {
  return (
    <div className="flex items-center justify-between mb-4">
      <div className="flex items-center gap-2">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-primary to-accent flex items-center justify-center">
          <span className="text-white font-bold text-sm">MF</span>
        </div>
        <div>
          <h1 className="text-base font-semibold gradient-text">MediaFusion</h1>
          {user && (
            <p className="text-xs text-muted-foreground">{user}</p>
          )}
        </div>
      </div>
      {onLogout && (
        <Button variant="ghost" size="icon" onClick={onLogout} title="Logout">
          <LogOut className="h-4 w-4" />
        </Button>
      )}
    </div>
  )
}

export default App
