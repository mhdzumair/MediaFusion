import { useState, useMemo } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipTrigger, TooltipProvider } from '@/components/ui/tooltip'
import {
  Rss,
  Plus,
  Play,
  Pause,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  Search,
  Hash,
  Users,
  AlertTriangle,
  Calendar,
  SlidersHorizontal,
} from 'lucide-react'
import { useRssFeeds, useRunRssScraper, useRssSchedulerStatus } from '@/hooks'
import { useAuth } from '@/contexts/AuthContext'
import type { UserRSSFeed } from '@/lib/api'
import { RSSFeedWizard, RSSFeedCard } from './components'
import { formatDistanceToNow } from 'date-fns'

export function RSSManagerPage() {
  const { user } = useAuth()
  const isAdmin = user?.role === 'admin'

  const [wizardOpen, setWizardOpen] = useState(false)
  const [editingFeed, setEditingFeed] = useState<UserRSSFeed | null>(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [filterStatus, setFilterStatus] = useState<'all' | 'active' | 'inactive'>('all')
  const [filterUser, setFilterUser] = useState<string>('all')

  const { data: feeds, isLoading, refetch } = useRssFeeds()
  const { data: schedulerStatus } = useRssSchedulerStatus()
  const runScraper = useRunRssScraper()

  const handleOpenWizard = (feed?: UserRSSFeed) => {
    setEditingFeed(feed || null)
    setWizardOpen(true)
  }

  const handleCloseWizard = () => {
    setWizardOpen(false)
    setEditingFeed(null)
  }

  const handleSuccess = () => {
    refetch()
  }

  // Get unique users for admin filter
  const uniqueUsers = useMemo(() => {
    if (!isAdmin || !feeds) return []
    const users = new Map<string, { id: string; email: string; username?: string }>()
    feeds.forEach((feed) => {
      if (feed.user) {
        users.set(feed.user.id, feed.user)
      }
    })
    return Array.from(users.values())
  }, [feeds, isAdmin])

  // Filter feeds
  const filteredFeeds = useMemo(() => {
    if (!feeds) return []

    return feeds.filter((feed) => {
      // Search filter
      if (searchQuery) {
        const query = searchQuery.toLowerCase()
        if (
          !feed.name.toLowerCase().includes(query) &&
          !feed.url.toLowerCase().includes(query) &&
          !feed.source?.toLowerCase().includes(query)
        ) {
          return false
        }
      }

      // Status filter
      if (filterStatus === 'active' && !feed.is_active) return false
      if (filterStatus === 'inactive' && feed.is_active) return false

      // User filter (admin only)
      if (isAdmin && filterUser !== 'all' && feed.user_id !== filterUser) {
        return false
      }

      return true
    })
  }, [feeds, searchQuery, filterStatus, filterUser, isAdmin])

  // Aggregated stats
  const stats = useMemo(() => {
    if (!feeds) return { total: 0, active: 0, inactive: 0, totalProcessed: 0, totalErrors: 0 }

    return {
      total: feeds.length,
      active: feeds.filter((f) => f.is_active).length,
      inactive: feeds.filter((f) => !f.is_active).length,
      totalProcessed: feeds.reduce((sum, f) => sum + (f.metrics?.total_items_processed || 0), 0),
      totalErrors: feeds.reduce((sum, f) => sum + (f.metrics?.total_errors || 0), 0),
    }
  }, [feeds])

  return (
    <TooltipProvider>
      <div className="space-y-6">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
              <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
                <Rss className="h-5 w-5 text-white" />
              </div>
              RSS Manager
            </h1>
            <p className="text-muted-foreground mt-1">
              {isAdmin
                ? 'Manage all RSS feed subscriptions across users'
                : 'Manage your RSS feed subscriptions for automatic content updates'}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            {isAdmin && (
              <Button variant="outline" size="sm" onClick={() => runScraper.mutate()} disabled={runScraper.isPending}>
                {runScraper.isPending ? (
                  <RefreshCw className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Play className="mr-2 h-4 w-4" />
                )}
                Run All
              </Button>
            )}
            <Button
              onClick={() => handleOpenWizard()}
              className="bg-gradient-to-r from-primary to-primary/80 hover:from-primary/90 hover:to-primary/70"
            >
              <Plus className="mr-2 h-4 w-4" />
              Add Feed
            </Button>
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          <StatsCard label="Total Feeds" value={stats.total} icon={<Rss className="h-4 w-4" />} color="violet" />
          <StatsCard label="Active" value={stats.active} icon={<CheckCircle className="h-4 w-4" />} color="emerald" />
          <StatsCard label="Paused" value={stats.inactive} icon={<Pause className="h-4 w-4" />} color="amber" />
          <StatsCard
            label="Items Processed"
            value={stats.totalProcessed}
            icon={<Hash className="h-4 w-4" />}
            color="blue"
          />
          <StatsCard
            label="Total Errors"
            value={stats.totalErrors}
            icon={<AlertTriangle className="h-4 w-4" />}
            color="red"
          />
        </div>

        {/* Scheduler Status */}
        {schedulerStatus && (
          <Card className="border-border/50">
            <CardContent className="py-3 px-4">
              <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="flex items-center gap-4 text-sm">
                  <Badge variant={schedulerStatus.enabled ? 'default' : 'secondary'}>
                    {schedulerStatus.enabled ? 'Scheduler Active' : 'Scheduler Disabled'}
                  </Badge>
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    Crontab: <code className="font-mono text-xs">{schedulerStatus.crontab}</code>
                  </span>
                </div>
                {schedulerStatus.next_run && (
                  <Tooltip>
                    <TooltipTrigger className="text-sm text-muted-foreground flex items-center gap-1">
                      <Calendar className="h-3 w-3" />
                      Next run: {formatDistanceToNow(new Date(schedulerStatus.next_run), { addSuffix: true })}
                    </TooltipTrigger>
                    <TooltipContent>{new Date(schedulerStatus.next_run).toLocaleString()}</TooltipContent>
                  </Tooltip>
                )}
              </div>
            </CardContent>
          </Card>
        )}

        {/* Filters */}
        <div className="flex flex-wrap gap-3 items-center">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search feeds..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="pl-10"
            />
          </div>

          <Select value={filterStatus} onValueChange={(v) => setFilterStatus(v as 'all' | 'active' | 'inactive')}>
            <SelectTrigger className="w-36">
              <SlidersHorizontal className="mr-2 h-4 w-4" />
              <SelectValue placeholder="Status" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Status</SelectItem>
              <SelectItem value="active">Active</SelectItem>
              <SelectItem value="inactive">Inactive</SelectItem>
            </SelectContent>
          </Select>

          {isAdmin && uniqueUsers.length > 0 && (
            <Select value={filterUser} onValueChange={setFilterUser}>
              <SelectTrigger className="w-48">
                <Users className="mr-2 h-4 w-4" />
                <SelectValue placeholder="Filter by user" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Users</SelectItem>
                {uniqueUsers.map((u) => (
                  <SelectItem key={u.id} value={u.id}>
                    {u.username || u.email}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}

          {(searchQuery || filterStatus !== 'all' || filterUser !== 'all') && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearchQuery('')
                setFilterStatus('all')
                setFilterUser('all')
              }}
            >
              <XCircle className="mr-2 h-4 w-4" />
              Clear Filters
            </Button>
          )}
        </div>

        {/* Feeds Grid */}
        {isLoading ? (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {[...Array(6)].map((_, i) => (
              <Card key={i} className="p-4">
                <div className="space-y-3">
                  <div className="flex items-center gap-3">
                    <Skeleton className="h-10 w-10 rounded-lg" />
                    <div className="flex-1 space-y-2">
                      <Skeleton className="h-4 w-2/3" />
                      <Skeleton className="h-3 w-1/2" />
                    </div>
                  </div>
                  <Skeleton className="h-3 w-full" />
                  <div className="grid grid-cols-4 gap-2">
                    {[...Array(4)].map((_, j) => (
                      <Skeleton key={j} className="h-12" />
                    ))}
                  </div>
                </div>
              </Card>
            ))}
          </div>
        ) : filteredFeeds.length === 0 ? (
          <Card className="border-border/50">
            <CardContent className="py-12 text-center">
              <Rss className="h-12 w-12 mx-auto mb-4 text-muted-foreground/50" />
              {searchQuery || filterStatus !== 'all' || filterUser !== 'all' ? (
                <>
                  <p className="text-muted-foreground">No feeds match your filters.</p>
                  <Button
                    variant="link"
                    className="mt-2"
                    onClick={() => {
                      setSearchQuery('')
                      setFilterStatus('all')
                      setFilterUser('all')
                    }}
                  >
                    Clear filters
                  </Button>
                </>
              ) : (
                <>
                  <p className="text-muted-foreground">No RSS feeds yet.</p>
                  <p className="text-sm text-muted-foreground mt-2">
                    Add your first RSS feed to start receiving automatic content updates.
                  </p>
                  <Button className="mt-4" onClick={() => handleOpenWizard()}>
                    <Plus className="mr-2 h-4 w-4" />
                    Add Feed
                  </Button>
                </>
              )}
            </CardContent>
          </Card>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {filteredFeeds.map((feed) => (
              <RSSFeedCard key={feed.id} feed={feed} onEdit={() => handleOpenWizard(feed)} showOwner={isAdmin} />
            ))}
          </div>
        )}

        {/* Results count */}
        {filteredFeeds.length > 0 && (
          <p className="text-sm text-muted-foreground text-center">
            Showing {filteredFeeds.length} of {feeds?.length || 0} feeds
          </p>
        )}

        {/* Feed Wizard */}
        <RSSFeedWizard open={wizardOpen} onClose={handleCloseWizard} feed={editingFeed} onSuccess={handleSuccess} />
      </div>
    </TooltipProvider>
  )
}

function StatsCard({
  label,
  value,
  icon,
  color,
}: {
  label: string
  value: number
  icon: React.ReactNode
  color: 'violet' | 'emerald' | 'amber' | 'blue' | 'red'
}) {
  const colorClasses = {
    violet: 'bg-primary/10 text-primary',
    emerald: 'bg-emerald-500/10 text-emerald-500',
    amber: 'bg-primary/10 text-primary',
    blue: 'bg-blue-500/10 text-blue-500',
    red: 'bg-red-500/10 text-red-500',
  }

  return (
    <Card className="border-border/50">
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <div className={`p-2 rounded-lg ${colorClasses[color]}`}>{icon}</div>
          <div>
            <p className="text-2xl font-bold">{value.toLocaleString()}</p>
            <p className="text-xs text-muted-foreground">{label}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
