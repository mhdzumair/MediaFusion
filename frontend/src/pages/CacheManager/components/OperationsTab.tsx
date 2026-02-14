import { useState } from 'react'
import {
  Trash2,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Loader2,
  Clock,
  User,
  Radio,
  FileJson,
  Database,
  Film,
  Server,
  Users,
  Calendar,
  Layers,
  Search,
  Shield,
  Image,
  Zap,
} from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import { useClearCache } from '../hooks/useCacheData'
import { CACHE_TYPES, getTypeColorClasses, type ActionHistoryItem } from '../types'

// Icon mapping for cache types
const iconMap: Record<string, React.ComponentType<{ className?: string }>> = {
  Radio,
  FileJson,
  Database,
  Film,
  Server,
  Users,
  Calendar,
  Layers,
  Search,
  Clock,
  Zap,
  Image,
  Shield,
}

interface OperationsTabProps {
  actionHistory: ActionHistoryItem[]
  onActionComplete: (action: ActionHistoryItem) => void
}

// Clear cache button with confirmation
function ClearCacheButton({
  name,
  pattern,
  icon,
  color,
  description,
  onClear,
  isClearing,
}: {
  name: string
  pattern: string
  icon: string
  color: string
  description: string
  onClear: (pattern: string, name: string) => Promise<void>
  isClearing: boolean
}) {
  const IconComponent = iconMap[icon] || Database
  const colors = getTypeColorClasses(color)

  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          variant="outline"
          className={cn('h-auto p-4 flex flex-col items-start gap-2 hover:border-destructive/50', colors.border)}
          disabled={isClearing}
        >
          <div className="flex items-center gap-2 w-full">
            <div className={cn('p-2 rounded-lg', colors.bg)}>
              <IconComponent className={cn('h-4 w-4', colors.text)} />
            </div>
            <span className="font-medium">{name}</span>
            <Trash2 className="h-4 w-4 ml-auto text-muted-foreground" />
          </div>
          <p className="text-xs text-muted-foreground text-left">{description}</p>
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-primary" />
            Clear {name} Cache
          </AlertDialogTitle>
          <AlertDialogDescription>
            This will delete all cache keys matching the pattern:
            <code className="block mt-2 p-2 bg-muted rounded text-sm font-mono">{pattern}</code>
            This action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            onClick={() => onClear(pattern, name)}
            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
          >
            Clear Cache
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

export function OperationsTab({ actionHistory, onActionComplete }: OperationsTabProps) {
  const { toast } = useToast()
  const clearCache = useClearCache()
  const [clearingPattern, setClearingPattern] = useState<string | null>(null)

  const handleClearCache = async (pattern: string, name: string) => {
    setClearingPattern(pattern)

    try {
      const result = await clearCache.mutateAsync({ pattern })

      const action: ActionHistoryItem = {
        id: Date.now().toString(),
        action: 'clear',
        target: name,
        timestamp: new Date(),
        result: `Deleted ${result.keys_deleted} keys`,
        admin: result.admin_username,
      }
      onActionComplete(action)

      toast({
        title: 'Cache Cleared',
        description: `Deleted ${result.keys_deleted} keys from ${name} cache`,
      })
    } catch (err) {
      const action: ActionHistoryItem = {
        id: Date.now().toString(),
        action: 'clear',
        target: name,
        timestamp: new Date(),
        result: 'Failed',
      }
      onActionComplete(action)

      toast({
        title: 'Error',
        description: `Failed to clear ${name} cache`,
        variant: 'destructive',
      })
    } finally {
      setClearingPattern(null)
    }
  }

  const handleClearAll = async () => {
    setClearingPattern('all')

    try {
      const result = await clearCache.mutateAsync({ pattern: '*' })

      const action: ActionHistoryItem = {
        id: Date.now().toString(),
        action: 'clear_all',
        target: 'All Caches',
        timestamp: new Date(),
        result: `Deleted ${result.keys_deleted} keys`,
        admin: result.admin_username,
      }
      onActionComplete(action)

      toast({
        title: 'All Caches Cleared',
        description: `Deleted ${result.keys_deleted} keys`,
      })
    } catch (err) {
      const action: ActionHistoryItem = {
        id: Date.now().toString(),
        action: 'clear_all',
        target: 'All Caches',
        timestamp: new Date(),
        result: 'Failed',
      }
      onActionComplete(action)

      toast({
        title: 'Error',
        description: 'Failed to clear all caches',
        variant: 'destructive',
      })
    } finally {
      setClearingPattern(null)
    }
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* Clear by Type */}
      <div className="lg:col-span-2 space-y-4">
        <div>
          <h3 className="text-lg font-semibold mb-1">Clear by Category</h3>
          <p className="text-sm text-muted-foreground">
            Clear specific cache categories. This action cannot be undone.
          </p>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {CACHE_TYPES.map((cacheType) => (
            <ClearCacheButton
              key={cacheType.name}
              {...cacheType}
              onClear={handleClearCache}
              isClearing={clearingPattern === cacheType.pattern}
            />
          ))}
        </div>

        {/* Clear All */}
        <div className="pt-4 border-t">
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" className="w-full gap-2" disabled={clearingPattern === 'all'}>
                {clearingPattern === 'all' ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
                Clear All Caches
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle className="flex items-center gap-2">
                  <AlertTriangle className="h-5 w-5 text-destructive" />
                  Clear ALL Caches
                </AlertDialogTitle>
                <AlertDialogDescription>
                  This will delete ALL cache keys in Redis. This is a destructive operation and will affect system
                  performance until caches are rebuilt.
                  <br />
                  <br />
                  Are you absolutely sure?
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleClearAll}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                >
                  Yes, Clear Everything
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </div>
      </div>

      {/* Action History */}
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-semibold mb-1">Recent Actions</h3>
          <p className="text-sm text-muted-foreground">History of cache operations</p>
        </div>

        <Card className="bg-card/50 border-border/50">
          <CardContent className="p-0">
            <ScrollArea className="h-[400px]">
              {actionHistory.length === 0 ? (
                <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                  <Clock className="h-8 w-8 mb-2 opacity-50" />
                  <p className="text-sm">No recent actions</p>
                </div>
              ) : (
                <div className="divide-y divide-border/50">
                  {actionHistory.map((action) => (
                    <div key={action.id} className="p-3 hover:bg-muted/30">
                      <div className="flex items-start gap-3">
                        <div
                          className={cn(
                            'p-1.5 rounded-full mt-0.5',
                            action.result.includes('Failed') ? 'bg-destructive/10' : 'bg-emerald-500/10',
                          )}
                        >
                          {action.result.includes('Failed') ? (
                            <XCircle className="h-4 w-4 text-destructive" />
                          ) : (
                            <CheckCircle className="h-4 w-4 text-emerald-400" />
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <p className="text-sm font-medium">
                              {action.action === 'clear_all' ? 'Clear All' : 'Clear'} {action.target}
                            </p>
                          </div>
                          <p className="text-xs text-muted-foreground mt-0.5">{action.result}</p>
                          <div className="flex items-center gap-2 mt-1">
                            <span className="text-[10px] text-muted-foreground">
                              {action.timestamp.toLocaleTimeString()}
                            </span>
                            {action.admin && (
                              <>
                                <span className="text-[10px] text-muted-foreground">•</span>
                                <span className="text-[10px] text-muted-foreground flex items-center gap-1">
                                  <User className="h-3 w-3" />
                                  {action.admin}
                                </span>
                              </>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
