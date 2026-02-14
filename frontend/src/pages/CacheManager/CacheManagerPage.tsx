import { useState, useCallback } from 'react'
import { HardDrive } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { OverviewTab, BrowseTab, OperationsTab, KeyDetailDialog } from './components'
import type { ActionHistoryItem } from './types'

export function CacheManagerPage() {
  const [activeTab, setActiveTab] = useState('overview')
  const [searchPattern, setSearchPattern] = useState('')
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [actionHistory, setActionHistory] = useState<ActionHistoryItem[]>([])

  // Handle clicking on a cache type card to browse its keys
  const handleCacheTypeClick = useCallback((pattern: string) => {
    setSearchPattern(pattern)
    setActiveTab('browse')
  }, [])

  // Handle viewing a specific key
  const handleViewKey = useCallback((key: string) => {
    setSelectedKey(key)
    setDialogOpen(true)
  }, [])

  // Handle action completion (for history)
  const handleActionComplete = useCallback((action: ActionHistoryItem) => {
    setActionHistory((prev) => [action, ...prev].slice(0, 50)) // Keep last 50 actions
  }, [])

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2.5 rounded-xl bg-primary/10">
          <HardDrive className="h-6 w-6 text-primary" />
        </div>
        <div>
          <h1 className="text-2xl font-bold">Cache Manager</h1>
          <p className="text-muted-foreground">Monitor and manage Redis cache data</p>
        </div>
      </div>

      {/* Main Content */}
      <Card className="border-border/50">
        <CardContent className="p-6">
          <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
            <TabsList className="grid w-full max-w-md grid-cols-3">
              <TabsTrigger value="overview">Overview</TabsTrigger>
              <TabsTrigger value="browse">Browse Keys</TabsTrigger>
              <TabsTrigger value="operations">Operations</TabsTrigger>
            </TabsList>

            <TabsContent value="overview" className="mt-6">
              <OverviewTab onCacheTypeClick={handleCacheTypeClick} />
            </TabsContent>

            <TabsContent value="browse" className="mt-6">
              <BrowseTab initialPattern={searchPattern} onViewKey={handleViewKey} />
            </TabsContent>

            <TabsContent value="operations" className="mt-6">
              <OperationsTab actionHistory={actionHistory} onActionComplete={handleActionComplete} />
            </TabsContent>
          </Tabs>
        </CardContent>
      </Card>

      {/* Key Detail Dialog */}
      <KeyDetailDialog
        cacheKey={selectedKey}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onDeleted={() => {
          const action: ActionHistoryItem = {
            id: Date.now().toString(),
            action: 'delete',
            target: selectedKey || 'Unknown',
            timestamp: new Date(),
            result: 'Deleted',
          }
          handleActionComplete(action)
        }}
      />
    </div>
  )
}
