import { useState, useEffect } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ImportTab } from '@/components/ImportTab'
import { SettingsTab } from '@/components/SettingsTab'
import { BulkUploadTab } from '@/components/BulkUploadTab'
import { storage } from '@/lib/storage'
import type { ExtensionSettings, PrefilledData } from '@/lib/types'
import { Upload, Settings, LogOut, AlertCircle, Layers, ArrowLeft } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'

// Type for bulk upload data stored in extension storage
interface BulkUploadData {
  torrents: Array<{
    magnetLink?: string
    url?: string
    type?: 'magnet' | 'torrent'
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
  const [isTabView, setIsTabView] = useState(false)
  const [advancedPrefilledData, setAdvancedPrefilledData] = useState<PrefilledData | null>(null)
  const [advancedBulkContext, setAdvancedBulkContext] = useState<{ itemKey: string; title: string } | null>(null)
  const [advancedCompletionEvent, setAdvancedCompletionEvent] = useState<{
    itemKey: string
    matchTitle?: string
    matchId?: string
    completedAt: number
  } | null>(null)
  const [advancedImportKey, setAdvancedImportKey] = useState(0)
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

  useEffect(() => {
    document.body.classList.toggle('mediafusion-tab-view', isTabView)
    document.documentElement.classList.toggle('mediafusion-tab-view', isTabView)

    return () => {
      document.body.classList.remove('mediafusion-tab-view')
      document.documentElement.classList.remove('mediafusion-tab-view')
    }
  }, [isTabView])

  async function loadData() {
    try {
      // Check URL parameters for bulk mode
      const urlParams = new URLSearchParams(window.location.search)
      const isBulk = urlParams.get('bulk') === 'true'
      const viewMode = urlParams.get('view')
      setIsBulkMode(isBulk)
      setIsTabView(viewMode === 'tab' || isBulk)

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
    try {
      if (typeof browser !== 'undefined' && browser.storage) {
        // Firefox mobile can be inconsistent with storage.session; local is safer.
        const firefoxStorage = browser.storage.local ?? browser.storage.session
        if (!firefoxStorage) {
          return null
        }

        const result = await firefoxStorage.get(['bulkUploadData']) as { bulkUploadData?: BulkUploadData }
        if (!result.bulkUploadData) {
          return null
        }

        await firefoxStorage.remove(['bulkUploadData'])
        return result.bulkUploadData
      }

      if (typeof chrome !== 'undefined' && chrome.storage) {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const chromeStorage = chrome.storage as any
        const storageApi = chromeStorage.session ?? chromeStorage.local
        if (!storageApi) {
          return null
        }

        const result = await new Promise<{ bulkUploadData?: BulkUploadData }>((resolve, reject) => {
          storageApi.get(['bulkUploadData'], (data: { bulkUploadData?: BulkUploadData }) => {
            if (chrome.runtime?.lastError) {
              reject(new Error(chrome.runtime.lastError.message))
              return
            }
            resolve(data ?? {})
          })
        })

        if (!result.bulkUploadData) {
          return null
        }

        await new Promise<void>((resolve, reject) => {
          storageApi.remove(['bulkUploadData'], () => {
            if (chrome.runtime?.lastError) {
              reject(new Error(chrome.runtime.lastError.message))
              return
            }
            resolve()
          })
        })

        return result.bulkUploadData
      }

      return null
    } catch (error) {
      console.error('Failed to load bulk upload data:', error)
      return null
    }
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

  function openAdvancedAnalyze(prefilledData: PrefilledData, context: { itemKey: string; title: string }) {
    setAdvancedPrefilledData(prefilledData)
    setAdvancedBulkContext(context)
    setAdvancedImportKey((prev) => prev + 1)
    setActiveTab('import')
  }

  function returnToBulk() {
    setAdvancedPrefilledData(null)
    setActiveTab('bulk')
  }

  function handleAdvancedImportComplete(details: { matchTitle?: string; matchId?: string }) {
    if (isBulkMode && advancedBulkContext) {
      setAdvancedCompletionEvent({
        itemKey: advancedBulkContext.itemKey,
        matchTitle: details.matchTitle || advancedBulkContext.title,
        matchId: details.matchId,
        completedAt: Date.now(),
      })
      setAdvancedBulkContext(null)
      setAdvancedPrefilledData(null)
      setActiveTab('bulk')
    }
  }

  const importPrefilledData = advancedPrefilledData ?? prefilledData

  const pageContainerClass = isTabView
    ? 'w-full min-h-dvh bg-background overflow-auto'
    : 'w-[380px] h-[500px] min-h-[500px] bg-background overflow-auto'

  const loadingContainerClass = isTabView
    ? 'flex items-center justify-center w-full min-h-dvh bg-background'
    : 'flex items-center justify-center w-[380px] h-[500px] min-h-[500px] bg-background'

  if (loading) {
    return (
      <div className={loadingContainerClass}>
        <div className="animate-pulse text-muted-foreground">Loading...</div>
      </div>
    )
  }

  const isConfigured = !!settings?.instanceUrl
  const isAuthenticated = !!settings?.authToken

  // If not configured, show settings only
  if (!isConfigured) {
    return (
      <div className={`${pageContainerClass} p-4`}>
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
    <div className={pageContainerClass}>
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
                <TabsTrigger value="import" className="flex-1 gap-1">
                  <Upload className="h-4 w-4" />
                  Import
                </TabsTrigger>
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
                settings={settings!}
                onOpenAdvancedAnalyze={openAdvancedAnalyze}
                advancedCompletionEvent={advancedCompletionEvent}
              />
            )}
          </TabsContent>
        )}

        <TabsContent value="import" className="mt-0 p-4 pt-2">
          {isBulkMode && bulkData && (
            <Button
              variant="outline"
              size="sm"
              className="mb-3 w-full"
              onClick={returnToBulk}
            >
              <ArrowLeft className="h-4 w-4 mr-2" />
              Back to Bulk Upload
            </Button>
          )}
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
              key={isBulkMode ? `bulk-import-${advancedImportKey}` : 'default-import'}
              settings={settings!}
              prefilledData={importPrefilledData}
              onImportComplete={isBulkMode ? handleAdvancedImportComplete : undefined}
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
      get(keys: string[] | string): Promise<Record<string, unknown>>
      remove(keys: string[] | string): Promise<void>
    }
    local: {
      get(keys: string[] | string): Promise<Record<string, unknown>>
      remove(keys: string[] | string): Promise<void>
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
