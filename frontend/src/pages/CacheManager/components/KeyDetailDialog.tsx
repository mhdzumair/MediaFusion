import { Eye, Copy, Trash2, Clock, HardDrive, Loader2, Hash, List, Layers, SortAsc, Type, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
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
import { useCacheKeyValue, useDeleteCacheKey, useDeleteCacheItem } from '../hooks/useCacheData'
// Note: deleteItem.isPending is available but not passed to viewers - they manage their own loading state
import { formatBytes, formatTTL, REDIS_TYPE_BADGES, type CacheValueResponse } from '../types'
import { HashViewer, ListViewer, ZSetViewer, StringViewer } from './viewers'

interface KeyDetailDialogProps {
  cacheKey: string | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onDeleted?: () => void
}

// Type badge component
function TypeBadge({ type }: { type: string }) {
  const typeInfo = REDIS_TYPE_BADGES[type] || REDIS_TYPE_BADGES.string
  const IconComponent = {
    Type,
    Hash,
    List,
    Layers,
    SortAsc,
  }[typeInfo.icon] || Type
  
  return (
    <Badge variant="outline" className={cn("gap-1.5 px-2.5 py-1", typeInfo.color)}>
      <IconComponent className="h-3.5 w-3.5" />
      {type.charAt(0).toUpperCase() + type.slice(1)}
    </Badge>
  )
}

// Value viewer dispatcher with delete handlers
interface ValueViewerProps {
  keyValue: CacheValueResponse
  onDeleteItem?: (params: { field?: string; member?: string; value?: string; index?: number }) => Promise<void>
}

function ValueViewer({ keyValue, onDeleteItem }: ValueViewerProps) {
  const { key, type, value, is_binary } = keyValue
  
  // Handle binary/image data
  if (is_binary) {
    return <StringViewer value={value} isBinary={true} cacheKey={key} />
  }
  
  // Handle different types
  switch (type) {
    case 'hash':
      if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
        return (
          <HashViewer 
            data={value as Record<string, string>}
            onDeleteItem={onDeleteItem ? (field) => onDeleteItem({ field }) : undefined}
          />
        )
      }
      break
    case 'list':
      if (Array.isArray(value)) {
        return (
          <ListViewer 
            data={value as string[]} 
            type="list"
            onDeleteItem={onDeleteItem}
          />
        )
      }
      break
    case 'set':
      if (Array.isArray(value)) {
        return (
          <ListViewer 
            data={value as string[]} 
            type="set"
            onDeleteItem={onDeleteItem}
          />
        )
      }
      break
    case 'zset':
      if (Array.isArray(value) && value.length > 0 && typeof value[0] === 'object' && 'member' in value[0]) {
        return (
          <ZSetViewer 
            data={value as Array<{ member: string; score: number }>}
            onDeleteItem={onDeleteItem ? (member) => onDeleteItem({ member }) : undefined}
          />
        )
      }
      break
  }
  
  // Default to string viewer
  return <StringViewer value={value} isBinary={false} cacheKey={key} />
}

export function KeyDetailDialog({ cacheKey, open, onOpenChange, onDeleted }: KeyDetailDialogProps) {
  const { toast } = useToast()
  const { data: keyValue, isLoading, error, refetch } = useCacheKeyValue(open ? cacheKey : null)
  const deleteKey = useDeleteCacheKey()
  const deleteItem = useDeleteCacheItem()
  
  const handleCopyKey = async () => {
    if (cacheKey) {
      await navigator.clipboard.writeText(cacheKey)
      toast({
        title: 'Copied',
        description: 'Key name copied to clipboard',
      })
    }
  }
  
  const handleDelete = async () => {
    if (!cacheKey) return
    
    try {
      await deleteKey.mutateAsync(cacheKey)
      toast({
        title: 'Deleted',
        description: 'Cache key deleted successfully',
      })
      onOpenChange(false)
      onDeleted?.()
    } catch (err) {
      toast({
        title: 'Error',
        description: 'Failed to delete cache key',
        variant: 'destructive',
      })
    }
  }
  
  const handleDeleteItem = async (params: { field?: string; member?: string; value?: string; index?: number }) => {
    if (!cacheKey) return
    
    try {
      await deleteItem.mutateAsync({ key: cacheKey, ...params })
      toast({
        title: 'Item Deleted',
        description: 'Item removed from the collection',
      })
      // Refetch to update the data
      refetch()
    } catch (err) {
      toast({
        title: 'Error',
        description: 'Failed to delete item',
        variant: 'destructive',
      })
      throw err // Re-throw so the viewer knows it failed
    }
  }
  
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-4xl max-h-[90vh] flex flex-col">
        <DialogHeader className="space-y-3">
          <div className="flex items-center gap-2">
            <Eye className="h-5 w-5 text-muted-foreground" />
            <DialogTitle className="text-xl">Cache Key Details</DialogTitle>
          </div>
          <DialogDescription className="font-mono text-sm break-all pr-8">
            {cacheKey}
          </DialogDescription>
        </DialogHeader>
        
        {isLoading ? (
          <div className="flex-1 flex items-center justify-center py-16">
            <div className="flex flex-col items-center gap-3">
              <Loader2 className="h-10 w-10 animate-spin text-muted-foreground" />
              <p className="text-sm text-muted-foreground">Loading key data...</p>
            </div>
          </div>
        ) : error ? (
          <div className="flex-1 flex items-center justify-center py-16">
            <div className="flex flex-col items-center gap-3 text-destructive">
              <AlertTriangle className="h-10 w-10" />
              <p className="text-sm">Failed to load key data</p>
            </div>
          </div>
        ) : keyValue ? (
          <>
            {/* Key metadata badges */}
            <div className="flex flex-wrap items-center gap-2 py-2">
              <TypeBadge type={keyValue.type} />
              <Badge variant="outline" className="gap-1.5 px-2.5 py-1">
                <Clock className="h-3.5 w-3.5" />
                TTL: {formatTTL(keyValue.ttl)}
              </Badge>
              <Badge variant="outline" className="gap-1.5 px-2.5 py-1">
                <HardDrive className="h-3.5 w-3.5" />
                Size: {formatBytes(keyValue.size)}
              </Badge>
              {keyValue.is_binary && (
                <Badge variant="secondary" className="bg-primary/20 text-primary border-primary/30 gap-1.5 px-2.5 py-1">
                  Binary
                </Badge>
              )}
            </div>
            
            {/* Value viewer - scrollable area */}
            <div className="flex-1 min-h-0 overflow-hidden">
              <ValueViewer 
                keyValue={keyValue} 
                onDeleteItem={handleDeleteItem}
              />
            </div>
          </>
        ) : null}
        
        <DialogFooter className="flex-shrink-0 flex items-center justify-between gap-3 pt-4 border-t">
          <Button
            variant="outline"
            onClick={handleCopyKey}
            className="gap-2"
          >
            <Copy className="h-4 w-4" />
            Copy Key Name
          </Button>
          
          <AlertDialog>
            <AlertDialogTrigger asChild>
              <Button variant="destructive" className="gap-2">
                <Trash2 className="h-4 w-4" />
                Delete Key
              </Button>
            </AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>Delete Cache Key</AlertDialogTitle>
                <AlertDialogDescription>
                  Are you sure you want to delete this cache key? This action cannot be undone.
                  <br />
                  <code className="mt-2 block text-xs bg-muted p-2 rounded break-all">
                    {cacheKey}
                  </code>
                </AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>Cancel</AlertDialogCancel>
                <AlertDialogAction
                  onClick={handleDelete}
                  className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  disabled={deleteKey.isPending}
                >
                  {deleteKey.isPending ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin mr-2" />
                      Deleting...
                    </>
                  ) : (
                    'Delete'
                  )}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
