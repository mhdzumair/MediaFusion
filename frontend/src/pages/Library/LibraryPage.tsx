import { useState, useEffect } from 'react'
import { useSearchParams, Link } from 'react-router-dom'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Compass, Bookmark, History, Library, Cloud, Settings2, Loader2 } from 'lucide-react'
import { BrowseTab, MyLibraryTab, HistoryTab, WatchlistTab } from './tabs'
import { useProfiles } from '@/hooks/useProfiles'

// Storage key for persisting library tab
const LIBRARY_TAB_KEY = 'library_active_tab'

export function LibraryPage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const { data: profiles, isLoading: profilesLoading } = useProfiles()
  const hasDebridProfile = profiles?.some((p) => p.streaming_providers?.has_debrid) ?? false

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

  if (profilesLoading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (!hasDebridProfile) {
    return (
      <div className="space-y-6 p-6 max-w-screen-xl mx-auto">
        <div className="space-y-2 animate-fade-in">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-md bg-primary/10 border border-primary/20">
              <Library className="h-5 w-5 text-primary" />
            </div>
            <h1 className="font-display text-3xl font-semibold tracking-tight">Library</h1>
          </div>
        </div>
        <Card className="max-w-lg mx-auto mt-12">
          <CardContent className="flex flex-col items-center gap-4 py-10 text-center">
            <Settings2 className="h-12 w-12 text-muted-foreground/50" />
            <h2 className="text-xl font-semibold">Streaming Provider Required</h2>
            <p className="text-muted-foreground max-w-sm">
              Configure a profile with at least one streaming provider (debrid service) to access library content.
            </p>
            <Button asChild>
              <Link to="/dashboard/configure">Configure a Profile</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

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
