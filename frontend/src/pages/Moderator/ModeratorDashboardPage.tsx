import { useSearchParams } from 'react-router-dom'
import { ArrowRightLeft, Clock, FileVideo, Film, Magnet, Settings, Shield, ThumbsDown, ThumbsUp } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAuth } from '@/contexts/AuthContext'
import {
  usePendingContributions,
  usePendingSuggestions,
  useStreamSuggestionStats,
  useStreamsNeedingAnnotation,
  useSuggestionStats,
} from '@/hooks'
import type { ContributionStatus, StreamSuggestionStatus, SuggestionStatus } from '@/lib/api'

import {
  AnnotationRequestsTab,
  ContributionsTab,
  ContributionSettingsTab,
  MediaMigrationTab,
  PendingSuggestionsTab,
  StreamSuggestionsTab,
  type ModeratorTab,
} from './components'

export function ModeratorDashboardPage() {
  const { user } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()

  const { data: pendingData } = usePendingSuggestions({ page: 1, page_size: 1 })
  const { data: suggestionStats } = useSuggestionStats()
  const { data: streamStats } = useStreamSuggestionStats()
  const { data: pendingContributions } = usePendingContributions({ page: 1, page_size: 1 })
  // Match the default annotation tab query so React Query can reuse
  // the same response and avoid duplicate heavy requests.
  const { data: annotationData } = useStreamsNeedingAnnotation({ page: 1, per_page: 20 })

  const pendingCount = pendingData?.total ?? 0
  const pendingContributionsCount = pendingContributions?.total ?? 0
  const streamPendingCount = streamStats?.pending ?? 0
  const annotationCount = annotationData?.total ?? 0

  const approvedToday = (suggestionStats?.approved_today ?? 0) + (streamStats?.approved_today ?? 0)
  const rejectedToday = (suggestionStats?.rejected_today ?? 0) + (streamStats?.rejected_today ?? 0)

  const isModerator = user?.role === 'moderator' || user?.role === 'admin'
  const isAdmin = user?.role === 'admin'

  const tabParam = searchParams.get('tab')
  const activeTab: ModeratorTab =
    tabParam === 'contributions' ||
    tabParam === 'annotations' ||
    tabParam === 'streams' ||
    tabParam === 'pending' ||
    tabParam === 'migration'
      ? tabParam
      : tabParam === 'settings' && isAdmin
        ? tabParam
        : 'contributions'

  const contributionStatusParam = searchParams.get('contentStatus')
  const contributionStatusFilter: 'all' | ContributionStatus =
    contributionStatusParam === 'all' ||
    contributionStatusParam === 'pending' ||
    contributionStatusParam === 'approved' ||
    contributionStatusParam === 'rejected'
      ? contributionStatusParam
      : 'pending'

  const streamStatusParam = searchParams.get('streamStatus')
  const streamStatusFilter: 'all' | StreamSuggestionStatus =
    streamStatusParam === 'all' ||
    streamStatusParam === 'pending' ||
    streamStatusParam === 'approved' ||
    streamStatusParam === 'auto_approved' ||
    streamStatusParam === 'rejected'
      ? streamStatusParam
      : 'all'

  const metadataStatusParam = searchParams.get('metadataStatus')
  const metadataStatusFilter: SuggestionStatus | 'all' =
    metadataStatusParam === 'all' ||
    metadataStatusParam === 'pending' ||
    metadataStatusParam === 'approved' ||
    metadataStatusParam === 'auto_approved' ||
    metadataStatusParam === 'rejected'
      ? metadataStatusParam
      : 'all'

  const updateModeratorParam = (key: string, value: string, defaultValue: string) => {
    const next = new URLSearchParams(searchParams)
    if (value === defaultValue) {
      next.delete(key)
    } else {
      next.set(key, value)
    }
    setSearchParams(next, { replace: true })
  }

  const handleTabChange = (value: string) => {
    const nextTab: ModeratorTab =
      value === 'contributions' ||
      value === 'annotations' ||
      value === 'streams' ||
      value === 'pending' ||
      value === 'migration' ||
      (value === 'settings' && isAdmin)
        ? (value as ModeratorTab)
        : 'contributions'
    updateModeratorParam('tab', nextTab, 'contributions')
  }

  if (!isModerator) {
    return (
      <div className="text-center py-12">
        <Shield className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
        <p className="mt-4 text-lg font-medium">Access Denied</p>
        <p className="text-sm text-muted-foreground mt-2">
          You need moderator or admin privileges to access this page.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
          <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
            <Shield className="h-5 w-5 text-white" />
          </div>
          Moderator Dashboard
        </h1>
        <p className="text-muted-foreground mt-1">Review and manage user-submitted metadata corrections</p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-orange-500/10">
                <Magnet className="h-4 w-4 text-orange-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{pendingContributionsCount}</p>
                <p className="text-xs text-muted-foreground">Content Imports</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-cyan-500/10">
                <FileVideo className="h-4 w-4 text-cyan-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{annotationCount}</p>
                <p className="text-xs text-muted-foreground">Annotations</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-primary/10">
                <Clock className="h-4 w-4 text-primary" />
              </div>
              <div>
                <p className="text-2xl font-bold">{pendingCount + streamPendingCount}</p>
                <p className="text-xs text-muted-foreground">Stream/Meta Edits</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-emerald-500/10">
                <ThumbsUp className="h-4 w-4 text-emerald-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{approvedToday}</p>
                <p className="text-xs text-muted-foreground">Approved Today</p>
              </div>
            </div>
          </CardContent>
        </Card>
        <Card className="glass border-border/50">
          <CardContent className="p-4">
            <div className="flex items-center gap-3">
              <div className="p-2 rounded-xl bg-red-500/10">
                <ThumbsDown className="h-4 w-4 text-red-500" />
              </div>
              <div>
                <p className="text-2xl font-bold">{rejectedToday}</p>
                <p className="text-xs text-muted-foreground">Rejected Today</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-6">
        <TabsList
          className={`h-auto p-1.5 bg-muted/50 rounded-xl grid grid-cols-2 ${
            user?.role === 'admin' ? 'sm:grid-cols-6' : 'sm:grid-cols-5'
          } gap-1 w-full`}
        >
          <TabsTrigger
            value="contributions"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Magnet className="mr-1.5 h-4 w-4" />
            Content Imports
            {pendingContributionsCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-orange-500/20 text-orange-600">
                {pendingContributionsCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="annotations"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <FileVideo className="mr-1.5 h-4 w-4" />
            File Annotations
            {annotationCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-cyan-500/20 text-cyan-600">
                {annotationCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="streams"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Film className="mr-1.5 h-4 w-4" />
            Streams
            {streamPendingCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-blue-500/20 text-blue-600">
                {streamPendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="pending"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <Clock className="mr-1.5 h-4 w-4" />
            Metadata
            {pendingCount > 0 && (
              <Badge variant="secondary" className="ml-1.5 h-5 px-1.5 text-xs bg-primary/20 text-primary">
                {pendingCount}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger
            value="migration"
            className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
          >
            <ArrowRightLeft className="mr-1.5 h-4 w-4" />
            Migration
          </TabsTrigger>
          {user?.role === 'admin' && (
            <TabsTrigger
              value="settings"
              className="rounded-lg data-[state=active]:bg-background data-[state=active]:shadow-sm py-2 px-3 text-sm"
            >
              <Settings className="mr-1.5 h-4 w-4" />
              Settings
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="contributions">
          <ContributionsTab
            statusFilter={contributionStatusFilter}
            onStatusFilterChange={(status) => updateModeratorParam('contentStatus', status, 'pending')}
          />
        </TabsContent>

        <TabsContent value="annotations">
          <AnnotationRequestsTab />
        </TabsContent>

        <TabsContent value="streams">
          <StreamSuggestionsTab
            statusFilter={streamStatusFilter}
            onStatusFilterChange={(status) => updateModeratorParam('streamStatus', status, 'all')}
          />
        </TabsContent>

        <TabsContent value="pending">
          <PendingSuggestionsTab
            statusFilter={metadataStatusFilter}
            onStatusFilterChange={(status) => updateModeratorParam('metadataStatus', status, 'all')}
          />
        </TabsContent>

        <TabsContent value="migration">
          <MediaMigrationTab />
        </TabsContent>

        {user?.role === 'admin' && (
          <TabsContent value="settings">
            <ContributionSettingsTab />
          </TabsContent>
        )}
      </Tabs>
    </div>
  )
}
