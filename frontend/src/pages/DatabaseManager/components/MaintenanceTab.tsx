import { useState } from 'react'
import {
  HardDrive,
  RefreshCw,
  Trash2,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Clock,
  Play,
  Loader2,
  Search,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Skeleton } from '@/components/ui/skeleton'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  useTableList,
  useVacuumTables,
  useAnalyzeTables,
  useReindexTables,
  useOrphanRecords,
  useCleanupOrphans,
} from '../hooks/useDatabaseData'
import { formatDuration, getTableTypeColor } from '../types'

interface OperationHistoryItem {
  id: string
  operation: string
  tables: string[]
  success: boolean
  message: string
  executionTime: number
  timestamp: Date
}

export function MaintenanceTab() {
  const [selectedTables, setSelectedTables] = useState<string[]>([])
  const [operationHistory, setOperationHistory] = useState<OperationHistoryItem[]>([])
  const [confirmDialog, setConfirmDialog] = useState<{
    open: boolean
    operation: 'vacuum' | 'vacuum_full' | 'analyze' | 'reindex' | 'cleanup'
    title: string
    description: string
  } | null>(null)

  const { toast } = useToast()

  // Queries
  const { data: tables, isLoading: tablesLoading } = useTableList()
  const { data: orphans, isLoading: orphansLoading, refetch: refetchOrphans } = useOrphanRecords()

  // Mutations
  const vacuumMutation = useVacuumTables()
  const analyzeMutation = useAnalyzeTables()
  const reindexMutation = useReindexTables()
  const cleanupMutation = useCleanupOrphans()

  const isOperationRunning =
    vacuumMutation.isPending || analyzeMutation.isPending || reindexMutation.isPending || cleanupMutation.isPending

  // Add to operation history
  const addToHistory = (
    operation: string,
    tables: string[],
    result: { success: boolean; message: string; execution_time_ms: number; tables_processed?: string[] },
  ) => {
    const item: OperationHistoryItem = {
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 9)}`,
      operation,
      tables,
      success: result.success,
      message: result.message,
      executionTime: result.execution_time_ms,
      timestamp: new Date(),
    }
    setOperationHistory((prev) => [item, ...prev].slice(0, 20))
  }

  // Handle table selection
  const handleSelectAll = () => {
    if (!tables?.tables) return
    if (selectedTables.length === tables.tables.length) {
      setSelectedTables([])
    } else {
      setSelectedTables(tables.tables.map((t) => t.name))
    }
  }

  const handleTableSelect = (tableName: string) => {
    setSelectedTables((prev) => (prev.includes(tableName) ? prev.filter((t) => t !== tableName) : [...prev, tableName]))
  }

  // Execute operations
  const executeOperation = async () => {
    if (!confirmDialog) return

    const tablesToProcess = selectedTables.length > 0 ? selectedTables : undefined

    try {
      let result

      switch (confirmDialog.operation) {
        case 'vacuum':
          result = await vacuumMutation.mutateAsync({
            tables: tablesToProcess,
            operation: 'vacuum',
            full: false,
          })
          break
        case 'vacuum_full':
          result = await vacuumMutation.mutateAsync({
            tables: tablesToProcess,
            operation: 'vacuum',
            full: true,
          })
          break
        case 'analyze':
          result = await analyzeMutation.mutateAsync({
            tables: tablesToProcess,
            operation: 'analyze',
          })
          break
        case 'reindex':
          result = await reindexMutation.mutateAsync({
            tables: tablesToProcess,
            operation: 'reindex',
          })
          break
        case 'cleanup': {
          const cleanupResult = await cleanupMutation.mutateAsync({ dry_run: false })
          result = {
            success: true,
            message: `Cleaned up: ${Object.entries(cleanupResult.deleted)
              .map(([k, v]) => `${k}: ${v}`)
              .join(', ')}`,
            execution_time_ms: 0,
            tables_processed: Object.keys(cleanupResult.deleted),
          }
          refetchOrphans()
          break
        }
      }

      if (result) {
        addToHistory(confirmDialog.operation, result.tables_processed || [], result)

        toast({
          title: 'Operation completed',
          description: result.message,
        })
      }
    } catch (error) {
      toast({
        title: 'Operation failed',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive',
      })
    }

    setConfirmDialog(null)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Left Column - Table Selection & Operations */}
      <div className="lg:col-span-2 space-y-6">
        {/* Operation Cards */}
        <div className="grid grid-cols-2 gap-4">
          <Card className="bg-card/50 border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <HardDrive className="h-4 w-4 text-blue-400" />
                VACUUM
              </CardTitle>
              <CardDescription className="text-xs">Reclaims storage and optimizes tables</CardDescription>
            </CardHeader>
            <CardContent className="space-y-2">
              <Button
                className="w-full"
                variant="outline"
                disabled={isOperationRunning}
                onClick={() =>
                  setConfirmDialog({
                    open: true,
                    operation: 'vacuum',
                    title: 'Run VACUUM',
                    description: `This will vacuum ${selectedTables.length > 0 ? selectedTables.length : 'all'} table(s). This may take some time.`,
                  })
                }
              >
                {vacuumMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Play className="h-4 w-4 mr-2" />
                )}
                VACUUM
              </Button>
              <Button
                className="w-full"
                variant="outline"
                disabled={isOperationRunning}
                onClick={() =>
                  setConfirmDialog({
                    open: true,
                    operation: 'vacuum_full',
                    title: 'Run VACUUM FULL',
                    description: `This will run VACUUM FULL on ${selectedTables.length > 0 ? selectedTables.length : 'all'} table(s). This requires exclusive lock and may take significant time.`,
                  })
                }
              >
                VACUUM FULL
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <RefreshCw className="h-4 w-4 text-emerald-400" />
                ANALYZE
              </CardTitle>
              <CardDescription className="text-xs">Updates statistics for query planning</CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                className="w-full"
                variant="outline"
                disabled={isOperationRunning}
                onClick={() =>
                  setConfirmDialog({
                    open: true,
                    operation: 'analyze',
                    title: 'Run ANALYZE',
                    description: `This will analyze ${selectedTables.length > 0 ? selectedTables.length : 'all'} table(s) to update statistics.`,
                  })
                }
              >
                {analyzeMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Play className="h-4 w-4 mr-2" />
                )}
                ANALYZE
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Search className="h-4 w-4 text-primary" />
                REINDEX
              </CardTitle>
              <CardDescription className="text-xs">Rebuilds indexes for better performance</CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                className="w-full"
                variant="outline"
                disabled={isOperationRunning}
                onClick={() =>
                  setConfirmDialog({
                    open: true,
                    operation: 'reindex',
                    title: 'Run REINDEX',
                    description: `This will reindex ${selectedTables.length > 0 ? selectedTables.length : 'all'} table(s). This may take time on large tables.`,
                  })
                }
              >
                {reindexMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Play className="h-4 w-4 mr-2" />
                )}
                REINDEX
              </Button>
            </CardContent>
          </Card>

          <Card className="bg-card/50 border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <Trash2 className="h-4 w-4 text-rose-400" />
                Orphan Cleanup
              </CardTitle>
              <CardDescription className="text-xs">Remove orphaned records</CardDescription>
            </CardHeader>
            <CardContent>
              <Button
                className="w-full"
                variant="outline"
                disabled={isOperationRunning || !orphans?.total_count}
                onClick={() =>
                  setConfirmDialog({
                    open: true,
                    operation: 'cleanup',
                    title: 'Clean Up Orphans',
                    description: `This will delete ${orphans?.total_count || 0} orphaned record(s). This action cannot be undone.`,
                  })
                }
              >
                {cleanupMutation.isPending ? (
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4 mr-2" />
                )}
                Clean Up ({orphans?.total_count || 0})
              </Button>
            </CardContent>
          </Card>
        </div>

        {/* Table Selection */}
        <Card className="bg-card/50 border-border/50">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium">Select Tables</CardTitle>
              <Button variant="ghost" size="sm" onClick={handleSelectAll}>
                {selectedTables.length === tables?.tables.length ? 'Deselect All' : 'Select All'}
              </Button>
            </div>
            <CardDescription>
              {selectedTables.length > 0
                ? `${selectedTables.length} table(s) selected`
                : 'No tables selected (operations will run on all tables)'}
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-48">
              <div className="space-y-1">
                {tablesLoading
                  ? [...Array(8)].map((_, i) => <Skeleton key={i} className="h-8 rounded-lg" />)
                  : tables?.tables.map((table) => {
                      const colors = getTableTypeColor(table.name)
                      return (
                        <div
                          key={table.name}
                          className={cn(
                            'flex items-center gap-3 p-2 rounded-lg transition-colors',
                            'hover:bg-muted/50',
                            selectedTables.includes(table.name) && 'bg-muted',
                          )}
                        >
                          <Checkbox
                            checked={selectedTables.includes(table.name)}
                            onCheckedChange={() => handleTableSelect(table.name)}
                          />
                          <span className={cn('text-sm font-mono', colors.text)}>{table.name}</span>
                          <span className="text-xs text-muted-foreground ml-auto">
                            {table.row_count.toLocaleString()} rows
                          </span>
                        </div>
                      )
                    })}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* Orphan Records */}
        <Card className="bg-card/50 border-border/50">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium flex items-center gap-2">
                <AlertTriangle className="h-4 w-4 text-primary" />
                Orphan Records
              </CardTitle>
              <Button variant="ghost" size="sm" onClick={() => refetchOrphans()}>
                <RefreshCw className="h-4 w-4" />
              </Button>
            </div>
            <CardDescription>Records without valid parent references</CardDescription>
          </CardHeader>
          <CardContent>
            {orphansLoading ? (
              <div className="space-y-2">
                {[...Array(3)].map((_, i) => (
                  <Skeleton key={i} className="h-12 rounded-lg" />
                ))}
              </div>
            ) : !orphans?.total_count ? (
              <div className="text-center py-8 text-muted-foreground">
                <CheckCircle className="h-8 w-8 mx-auto mb-2 text-emerald-400" />
                <p className="text-sm">No orphan records found</p>
              </div>
            ) : (
              <div className="space-y-3">
                {Object.entries(orphans.by_type).map(([table, count]) => (
                  <div key={table} className="flex items-center justify-between p-2 rounded-lg bg-muted/30">
                    <span className="font-mono text-sm">{table}</span>
                    <Badge variant="secondary" className="bg-rose-500/10 text-rose-400">
                      {count} orphans
                    </Badge>
                  </div>
                ))}
                <ScrollArea className="h-32 mt-2">
                  <div className="space-y-1">
                    {orphans.orphans.slice(0, 20).map((orphan, i) => (
                      <div key={i} className="text-xs p-2 rounded bg-muted/20">
                        <span className="font-mono text-muted-foreground">{orphan.table}</span>
                        <span className="mx-2">•</span>
                        <span className="font-mono">{orphan.id}</span>
                        <span className="mx-2">•</span>
                        <span className="text-muted-foreground">{orphan.reason}</span>
                      </div>
                    ))}
                  </div>
                </ScrollArea>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Right Column - Operation History */}
      <div>
        <Card className="bg-card/50 border-border/50 h-full">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Clock className="h-4 w-4 text-muted-foreground" />
              Operation History
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="h-[calc(100vh-400px)]">
              {operationHistory.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground">
                  <Clock className="h-8 w-8 mx-auto mb-2 opacity-50" />
                  <p className="text-sm">No operations yet</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {operationHistory.map((item) => (
                    <div key={item.id} className="p-3 rounded-lg bg-muted/30 space-y-2">
                      <div className="flex items-center justify-between">
                        <Badge
                          variant="outline"
                          className={cn(
                            'font-mono text-xs',
                            item.success
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                              : 'bg-rose-500/10 text-rose-400 border-rose-500/30',
                          )}
                        >
                          {item.operation.toUpperCase()}
                        </Badge>
                        {item.success ? (
                          <CheckCircle className="h-4 w-4 text-emerald-400" />
                        ) : (
                          <XCircle className="h-4 w-4 text-rose-400" />
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground line-clamp-2">{item.message}</p>
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>{formatDuration(item.executionTime)}</span>
                        <span>{item.timestamp.toLocaleTimeString()}</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      </div>

      {/* Confirmation Dialog */}
      <AlertDialog open={!!confirmDialog} onOpenChange={(open) => !open && setConfirmDialog(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmDialog?.title}</AlertDialogTitle>
            <AlertDialogDescription>{confirmDialog?.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={executeOperation}>
              {isOperationRunning ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  Running...
                </>
              ) : (
                'Execute'
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
