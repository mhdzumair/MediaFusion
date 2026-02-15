import { useState, useEffect } from 'react'
import { useSearchParams } from 'react-router-dom'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Compass, Bookmark, History, Library, Cloud } from 'lucide-react'
import { BrowseTab, MyLibraryTab, HistoryTab, WatchlistTab } from './tabs'

// Storage key for persisting library tab
const LIBRARY_TAB_KEY = 'library_active_tab'

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams()

  // Get initial tab from URL or session storage
  const urlTab = searchParams.get('tab')
  const storedTab = sessionStorage.getItem(LIBRARY_TAB_KEY)

  const [activeTab, setActiveTab] = useState(urlTab || storedTab || 'browse')

  // Update URL and storage when tab changes (user clicked a tab)
  const handleTabChange = (tab: string) => {
    setActiveTab(tab)
    setSearchParams({ tab }, { replace: true })
  }

  // Sync with URL tab param changes
  const explicitTab = searchParams.get('tab')
  const [prevExplicitTab, setPrevExplicitTab] = useState(explicitTab)
  if (explicitTab && explicitTab !== prevExplicitTab) {
    setPrevExplicitTab(explicitTab)
    setActiveTab(explicitTab)
  }

  // Persist active tab to sessionStorage
  useEffect(() => {
    sessionStorage.setItem(LIBRARY_TAB_KEY, activeTab)
  }, [activeTab])

  return (
    <div className="space-y-6 p-6 max-w-screen-xl mx-auto">
      {/* Header */}
      <div className="space-y-2 animate-fade-in">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
            <Library className="h-5 w-5 text-primary" />
          </div>
          <h1 className="font-display text-3xl font-semibold tracking-tight">Library</h1>
        </div>
        <p className="text-muted-foreground">Browse, discover and manage your content collection</p>
      </div>

      {/* Tabs */}
      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-6">
        <TabsList className="grid w-full max-w-lg grid-cols-4 animate-fade-in animate-delay-100">
          <TabsTrigger
            value="browse"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <Compass className="mr-2 h-4 w-4" />
            Browse
          </TabsTrigger>
          <TabsTrigger
            value="library"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <Bookmark className="mr-2 h-4 w-4" />
            My Library
          </TabsTrigger>
          <TabsTrigger
            value="watchlist"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <Cloud className="mr-2 h-4 w-4" />
            Watchlist
          </TabsTrigger>
          <TabsTrigger
            value="history"
            className="data-[state=active]:bg-primary data-[state=active]:text-primary-foreground"
          >
            <History className="mr-2 h-4 w-4" />
            History
          </TabsTrigger>
        </TabsList>

        <TabsContent value="browse" className="space-y-6 mt-0 animate-fade-in">
          <BrowseTab />
        </TabsContent>

        <TabsContent value="library" className="space-y-6 mt-0 animate-fade-in">
          <MyLibraryTab />
        </TabsContent>

        <TabsContent value="watchlist" className="space-y-6 mt-0 animate-fade-in">
          <WatchlistTab />
        </TabsContent>

        <TabsContent value="history" className="space-y-6 mt-0 animate-fade-in">
          <HistoryTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
