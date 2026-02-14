import { useState } from 'react'
import { 
  Database, 
  LayoutDashboard, 
  Table2, 
  Settings,
} from 'lucide-react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Badge } from '@/components/ui/badge'
import { useDatabaseStats, useTableList } from './hooks/useDatabaseData'
import {
  OverviewTab,
  TableBrowserTab,
  MaintenanceTab,
} from './components'
import type { DatabaseTab } from './types'

export function DatabaseManagerPage() {
  const [activeTab, setActiveTab] = useState<DatabaseTab>('overview')
  const [selectedTable, setSelectedTable] = useState<string | null>(null)
  
  const { data: stats } = useDatabaseStats()
  const { data: tables } = useTableList()
  
  // Handle table click from overview - switch to browser tab
  const handleTableClick = (tableName: string) => {
    setSelectedTable(tableName)
    setActiveTab('browser')
  }
  
  return (
    <div className="space-y-4 md:space-y-6">
      {/* Page Header - Responsive */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl md:text-2xl font-bold flex items-center gap-2 md:gap-3">
            <div className="p-1.5 md:p-2 rounded-lg md:rounded-xl bg-gradient-to-br from-primary/20 to-primary/10 shrink-0">
              <Database className="h-5 w-5 md:h-6 md:w-6 text-primary" />
            </div>
            <span className="truncate">Database Manager</span>
          </h1>
          <p className="text-muted-foreground text-sm mt-1 hidden sm:block">
            Manage database tables and perform maintenance operations
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {stats && (
            <Badge variant="outline" className="font-mono text-xs px-2 py-0.5 md:px-3 md:py-1">
              {stats.size_human}
            </Badge>
          )}
          {tables && (
            <Badge variant="outline" className="font-mono text-xs px-2 py-0.5 md:px-3 md:py-1">
              {tables.total_count} tables
            </Badge>
          )}
        </div>
      </div>
      
      {/* Tabs - Scrollable on mobile */}
      <Tabs 
        value={activeTab} 
        onValueChange={(v) => setActiveTab(v as DatabaseTab)} 
        className="space-y-3 md:space-y-4"
      >
        <TabsList className="glass border border-border/50 p-1 h-auto w-fit justify-start">
          <TabsTrigger 
            value="overview" 
            className="gap-1.5 md:gap-2 text-xs md:text-sm px-2 md:px-3 data-[state=active]:bg-primary/20 shrink-0"
          >
            <LayoutDashboard className="h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Overview</span>
          </TabsTrigger>
          <TabsTrigger 
            value="browser" 
            className="gap-1.5 md:gap-2 text-xs md:text-sm px-2 md:px-3 data-[state=active]:bg-blue-500/20 shrink-0"
          >
            <Table2 className="h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Tables</span>
          </TabsTrigger>
          <TabsTrigger 
            value="maintenance" 
            className="gap-1.5 md:gap-2 text-xs md:text-sm px-2 md:px-3 data-[state=active]:bg-primary/20 shrink-0"
          >
            <Settings className="h-3.5 w-3.5 md:h-4 md:w-4" />
            <span className="hidden sm:inline">Maintenance</span>
          </TabsTrigger>
        </TabsList>
        
        <TabsContent value="overview" className="mt-3 md:mt-4">
          <OverviewTab onTableClick={handleTableClick} />
        </TabsContent>
        
        <TabsContent value="browser" className="mt-3 md:mt-4">
          <TableBrowserTab initialTable={selectedTable || undefined} />
        </TabsContent>
        
        <TabsContent value="maintenance" className="mt-3 md:mt-4">
          <MaintenanceTab />
        </TabsContent>
      </Tabs>
    </div>
  )
}
