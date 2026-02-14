import { useState } from 'react'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
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
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip'
import {
  Rss,
  MoreVertical,
  Pencil,
  Trash2,
  Play,
  Clock,
  Hash,
  AlertTriangle,
  CheckCircle,
  XCircle,
  User,
  ExternalLink,
  Loader2,
  Copy,
} from 'lucide-react'
import type { UserRSSFeed } from '@/lib/api'
import { formatDistanceToNow } from 'date-fns'
import { useUpdateRssFeed, useDeleteRssFeed, useScrapeRssFeed } from '@/hooks'

interface RSSFeedCardProps {
  feed: UserRSSFeed
  onEdit: () => void
  showOwner?: boolean
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.floor(seconds % 60)
  return `${mins}m ${secs}s`
}

export function RSSFeedCard({ feed, onEdit, showOwner }: RSSFeedCardProps) {
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  
  const updateFeed = useUpdateRssFeed()
  const deleteFeed = useDeleteRssFeed()
  const scrapeFeed = useScrapeRssFeed()
  
  const handleToggleActive = async () => {
    await updateFeed.mutateAsync({
      feedId: feed.id,
      data: { is_active: !feed.is_active },
    })
  }
  
  const handleDelete = async () => {
    await deleteFeed.mutateAsync(feed.id)
    setDeleteDialogOpen(false)
  }
  
  const handleScrape = async () => {
    await scrapeFeed.mutateAsync(feed.id)
  }
  
  const copyUrl = () => {
    navigator.clipboard.writeText(feed.url)
  }
  
  const metrics = feed.metrics
  const isPending = updateFeed.isPending || deleteFeed.isPending || scrapeFeed.isPending
  
  return (
    <TooltipProvider>
      <Card className={`group transition-all hover:shadow-lg ${!feed.is_active ? 'opacity-60' : ''}`}>
        <CardHeader className="pb-2">
          <div className="flex items-start justify-between">
            <div className="flex items-center gap-3">
              <div className={`p-2 rounded-lg ${
                feed.is_active 
                  ? 'bg-gradient-to-br from-primary/20 to-primary/10' 
                  : 'bg-muted'
              }`}>
                <Rss className={`h-5 w-5 ${feed.is_active ? 'text-primary' : 'text-muted-foreground'}`} />
              </div>
              
              <div>
                <h3 className="font-semibold text-lg leading-tight">{feed.name}</h3>
                <div className="flex items-center gap-2 mt-1">
                  {showOwner && feed.user && (
                    <Badge variant="outline" className="text-xs">
                      <User className="h-3 w-3 mr-1" />
                      {feed.user.username || feed.user.email}
                    </Badge>
                  )}
                  <Badge variant="secondary" className="text-xs">
                    {feed.torrent_type}
                  </Badge>
                  {feed.source && (
                    <Badge variant="outline" className="text-xs">
                      {feed.source}
                    </Badge>
                  )}
                </div>
              </div>
            </div>
            
            <div className="flex items-center gap-2">
              <Switch
                checked={feed.is_active}
                onCheckedChange={handleToggleActive}
                disabled={isPending}
              />
              
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="icon" className="h-8 w-8">
                    <MoreVertical className="h-4 w-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={onEdit}>
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={handleScrape} disabled={scrapeFeed.isPending}>
                    <Play className="mr-2 h-4 w-4" />
                    Scrape Now
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={copyUrl}>
                    <Copy className="mr-2 h-4 w-4" />
                    Copy URL
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                    <a href={feed.url} target="_blank" rel="noopener noreferrer">
                      <ExternalLink className="mr-2 h-4 w-4" />
                      Open in Browser
                    </a>
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem 
                    onClick={() => setDeleteDialogOpen(true)}
                    className="text-red-500 focus:text-red-500"
                  >
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </CardHeader>
        
        <CardContent className="pt-2">
          {/* URL */}
          <div className="text-sm text-muted-foreground truncate mb-4 font-mono">
            {feed.url}
          </div>
          
          {/* Metrics Grid */}
          {metrics && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
              <MetricItem
                label="Items Found"
                value={metrics.total_items_found}
                icon={<Hash className="h-4 w-4" />}
                color="violet"
              />
              <MetricItem
                label="Processed"
                value={metrics.total_items_processed}
                icon={<CheckCircle className="h-4 w-4" />}
                color="emerald"
              />
              <MetricItem
                label="Skipped"
                value={metrics.total_items_skipped}
                icon={<XCircle className="h-4 w-4" />}
                color="amber"
              />
              <MetricItem
                label="Errors"
                value={metrics.total_errors}
                icon={<AlertTriangle className="h-4 w-4" />}
                color="red"
              />
            </div>
          )}
          
          {/* Last run info */}
          <div className="flex items-center justify-between text-xs text-muted-foreground border-t pt-3">
            <div className="flex items-center gap-4">
              {feed.last_scraped_at && (
                <Tooltip>
                  <TooltipTrigger className="flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    <span>Last run: {formatDistanceToNow(new Date(feed.last_scraped_at), { addSuffix: true })}</span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {new Date(feed.last_scraped_at).toLocaleString()}
                  </TooltipContent>
                </Tooltip>
              )}
              
              {metrics?.last_scrape_duration !== undefined && metrics.last_scrape_duration !== null && (
                <span className="flex items-center gap-1">
                  Duration: {formatDuration(metrics.last_scrape_duration)}
                </span>
              )}
            </div>
            
            {feed.auto_detect_catalog && (
              <Badge variant="outline" className="text-xs">
                Auto-detect
              </Badge>
            )}
          </div>
          
          {/* Last run stats */}
          {metrics && metrics.items_processed_last_run > 0 && (
            <div className="mt-2 text-xs text-muted-foreground">
              Last run: {metrics.items_processed_last_run} processed, {metrics.items_skipped_last_run} skipped, {metrics.errors_last_run} errors
            </div>
          )}
          
          {/* Skip reasons if any */}
          {metrics?.skip_reasons && Object.keys(metrics.skip_reasons).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {Object.entries(metrics.skip_reasons).map(([reason, count]) => (
                <Badge key={reason} variant="secondary" className="text-xs">
                  {reason}: {count}
                </Badge>
              ))}
            </div>
          )}
          
          {/* Loading overlay */}
          {isPending && (
            <div className="absolute inset-0 bg-background/80 flex items-center justify-center rounded-lg">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          )}
        </CardContent>
        
        <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Delete RSS Feed</AlertDialogTitle>
              <AlertDialogDescription>
                Are you sure you want to delete "{feed.name}"? This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                onClick={handleDelete}
                className="bg-red-500 hover:bg-red-600"
              >
                Delete
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </Card>
    </TooltipProvider>
  )
}

function MetricItem({ 
  label, 
  value, 
  icon, 
  color 
}: { 
  label: string
  value: number
  icon: React.ReactNode
  color: 'violet' | 'emerald' | 'amber' | 'red'
}) {
  const colorClasses = {
    violet: 'text-primary bg-primary/10',
    emerald: 'text-emerald-500 bg-emerald-500/10',
    amber: 'text-primary bg-primary/10',
    red: 'text-red-500 bg-red-500/10',
  }
  
  return (
    <div className="flex items-center gap-2">
      <div className={`p-1.5 rounded ${colorClasses[color]}`}>
        {icon}
      </div>
      <div>
        <div className="text-sm font-semibold">{value.toLocaleString()}</div>
        <div className="text-xs text-muted-foreground">{label}</div>
      </div>
    </div>
  )
}

