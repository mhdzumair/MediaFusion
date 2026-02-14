import { useState, useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Progress } from '@/components/ui/progress'
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
} from '@/components/ui/alert-dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import {
  FileVideo,
  Tv,
  MoreVertical,
  RefreshCw,
  Trash2,
  Edit,
  Clock,
  CheckCircle,
  XCircle,
  Loader2,
  Globe,
  Lock,
  Server,
  Film,
  MonitorPlay,
  Plus,
} from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'
import { Link } from 'react-router-dom'
import {
  useIPTVSources,
  useUpdateIPTVSource,
  useDeleteIPTVSource,
  useSyncIPTVSource,
  useImportJobStatus,
} from '@/hooks'
import type { IPTVSource, ImportJobStatus } from '@/lib/api'

const SOURCE_TYPE_ICONS = {
  m3u: FileVideo,
  xtream: Server,
  stalker: Tv,
}

const SOURCE_TYPE_LABELS = {
  m3u: 'M3U Playlist',
  xtream: 'Xtream Codes',
  stalker: 'Stalker Portal',
}

export function IPTVSourcesPage() {
  const [editingSource, setEditingSource] = useState<IPTVSource | null>(null)
  const [deletingSourceId, setDeletingSourceId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editImportLive, setEditImportLive] = useState(true)
  const [editImportVod, setEditImportVod] = useState(true)
  const [editImportSeries, setEditImportSeries] = useState(true)

  // Track active sync jobs by source ID
  const [syncJobs, setSyncJobs] = useState<Record<number, string>>({})
  const [completedJobs, setCompletedJobs] = useState<Record<number, ImportJobStatus | null>>({})

  const { data: sourcesData, isLoading, refetch } = useIPTVSources()
  const updateSource = useUpdateIPTVSource()
  const deleteSource = useDeleteIPTVSource()
  const syncSource = useSyncIPTVSource()

  // Get the currently active job ID (only one at a time for polling)
  const activeSourceId = Object.keys(syncJobs).find((id) => syncJobs[parseInt(id)])
    ? parseInt(Object.keys(syncJobs).find((id) => syncJobs[parseInt(id)])!)
    : null
  const activeJobId = activeSourceId ? syncJobs[activeSourceId] : null

  // Poll for job status
  const { data: jobStatus } = useImportJobStatus(activeJobId, {
    onComplete: (status) => {
      if (activeSourceId) {
        setCompletedJobs((prev) => ({ ...prev, [activeSourceId]: status }))
        setSyncJobs((prev) => {
          const next = { ...prev }
          delete next[activeSourceId]
          return next
        })
        // Refetch sources to get updated sync stats
        refetch()
      }
    },
    onError: (status) => {
      if (activeSourceId) {
        setCompletedJobs((prev) => ({ ...prev, [activeSourceId]: status }))
        setSyncJobs((prev) => {
          const next = { ...prev }
          delete next[activeSourceId]
          return next
        })
      }
    },
  })

  // Clear completed job notification after 5 seconds
  useEffect(() => {
    const completedIds = Object.keys(completedJobs).map(Number)
    if (completedIds.length > 0) {
      const timer = setTimeout(() => {
        setCompletedJobs({})
      }, 5000)
      return () => clearTimeout(timer)
    }
  }, [completedJobs])

  const handleEdit = (source: IPTVSource) => {
    setEditingSource(source)
    setEditName(source.name)
    setEditImportLive(source.import_live)
    setEditImportVod(source.import_vod)
    setEditImportSeries(source.import_series)
  }

  const handleSaveEdit = async () => {
    if (!editingSource) return

    await updateSource.mutateAsync({
      sourceId: editingSource.id,
      data: {
        name: editName,
        import_live: editImportLive,
        import_vod: editImportVod,
        import_series: editImportSeries,
      },
    })
    setEditingSource(null)
  }

  const handleDelete = async () => {
    if (!deletingSourceId) return

    await deleteSource.mutateAsync(deletingSourceId)
    setDeletingSourceId(null)
  }

  const handleSync = async (sourceId: number) => {
    try {
      const result = await syncSource.mutateAsync(sourceId)

      // Check if it's a background task
      if (result.status === 'processing' && result.job_id) {
        setSyncJobs((prev) => ({ ...prev, [sourceId]: result.job_id! }))
      } else if (result.status === 'success') {
        // Immediate sync completed, refetch sources
        refetch()
      }
    } catch (error) {
      console.error('Sync failed:', error)
    }
  }

  const sources = sourcesData?.sources || []

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight flex items-center gap-3">
            <div className="p-2 rounded-xl bg-gradient-to-br from-primary to-primary/80 shadow-lg shadow-primary/20">
              <Tv className="h-5 w-5 text-white" />
            </div>
            IPTV Sources
          </h1>
          <p className="text-muted-foreground mt-1">Manage your saved IPTV sources for easy re-sync</p>
        </div>
        <Button asChild className="rounded-xl bg-gradient-to-r from-primary to-primary/80">
          <Link to="/dashboard/import">
            <Plus className="mr-2 h-4 w-4" />
            Add Source
          </Link>
        </Button>
      </div>

      {/* Sources List */}
      {isLoading ? (
        <div className="flex items-center justify-center py-12">
          <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
        </div>
      ) : sources.length === 0 ? (
        <Card className="glass border-border/50">
          <CardContent className="flex flex-col items-center justify-center py-12">
            <div className="p-4 rounded-2xl bg-muted/50 mb-4">
              <FileVideo className="h-8 w-8 text-muted-foreground" />
            </div>
            <h3 className="text-lg font-medium mb-1">No sources saved</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Import an M3U playlist or Xtream server and save it for later sync
            </p>
            <Button asChild variant="outline" className="rounded-xl">
              <Link to="/dashboard/import">
                <Plus className="mr-2 h-4 w-4" />
                Import Content
              </Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid gap-4">
          {sources.map((source) => {
            const Icon = SOURCE_TYPE_ICONS[source.source_type] || FileVideo
            const isSyncing = (syncSource.isPending && syncSource.variables === source.id) || !!syncJobs[source.id]
            const currentJobId = syncJobs[source.id]
            const isActiveJob = currentJobId && activeJobId === currentJobId
            const completedJob = completedJobs[source.id]

            return (
              <Card key={source.id} className="glass border-border/50">
                <CardContent className="flex flex-col gap-3 p-4">
                  <div className="flex items-center gap-4">
                    {/* Icon */}
                    <div className="flex-shrink-0 w-12 h-12 rounded-xl bg-gradient-to-br from-primary/20 to-primary/10 flex items-center justify-center">
                      <Icon className="h-6 w-6 text-primary" />
                    </div>

                    {/* Info */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <h3 className="font-medium truncate">{source.name}</h3>
                        <Badge variant="outline" className="text-xs">
                          {SOURCE_TYPE_LABELS[source.source_type]}
                        </Badge>
                        {source.is_public ? (
                          <Badge variant="secondary" className="text-xs gap-1">
                            <Globe className="h-3 w-3" />
                            Public
                          </Badge>
                        ) : (
                          <Badge variant="secondary" className="text-xs gap-1">
                            <Lock className="h-3 w-3" />
                            Private
                          </Badge>
                        )}
                        {!source.is_active && (
                          <Badge variant="destructive" className="text-xs">
                            Inactive
                          </Badge>
                        )}
                      </div>

                      {/* Import types */}
                      <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
                        {source.import_live && (
                          <span className="flex items-center gap-1">
                            <Tv className="h-3 w-3" /> Live
                          </span>
                        )}
                        {source.import_vod && (
                          <span className="flex items-center gap-1">
                            <Film className="h-3 w-3" /> Movies
                          </span>
                        )}
                        {source.import_series && (
                          <span className="flex items-center gap-1">
                            <MonitorPlay className="h-3 w-3" /> Series
                          </span>
                        )}
                      </div>

                      {/* Last sync stats */}
                      {source.last_sync_stats && !isSyncing && (
                        <div className="flex items-center gap-2 mt-1 text-sm text-muted-foreground">
                          Last sync:
                          {source.last_sync_stats.tv !== undefined && (
                            <Badge variant="outline" className="text-xs">
                              {source.last_sync_stats.tv} TV
                            </Badge>
                          )}
                          {source.last_sync_stats.movie !== undefined && (
                            <Badge variant="outline" className="text-xs">
                              {source.last_sync_stats.movie} Movies
                            </Badge>
                          )}
                          {source.last_sync_stats.series !== undefined && (
                            <Badge variant="outline" className="text-xs">
                              {source.last_sync_stats.series} Series
                            </Badge>
                          )}
                        </div>
                      )}
                    </div>

                    {/* Last synced */}
                    <div className="flex-shrink-0 text-right text-sm text-muted-foreground">
                      {source.last_synced_at ? (
                        <div className="flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {formatDistanceToNow(new Date(source.last_synced_at), { addSuffix: true })}
                        </div>
                      ) : (
                        <span>Never synced</span>
                      )}
                    </div>

                    {/* Actions */}
                    <div className="flex-shrink-0 flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="rounded-xl"
                        onClick={() => handleSync(source.id)}
                        disabled={isSyncing || !source.is_active}
                      >
                        {isSyncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                        <span className="ml-2">Sync</span>
                      </Button>

                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button variant="ghost" size="icon" className="rounded-xl">
                            <MoreVertical className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => handleEdit(source)}>
                            <Edit className="h-4 w-4 mr-2" />
                            Edit
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setDeletingSourceId(source.id)} className="text-destructive">
                            <Trash2 className="h-4 w-4 mr-2" />
                            Delete
                          </DropdownMenuItem>
                        </DropdownMenuContent>
                      </DropdownMenu>
                    </div>
                  </div>

                  {/* Sync Progress */}
                  {isActiveJob && jobStatus && (
                    <div className="ml-16 space-y-2">
                      <div className="flex items-center justify-between text-sm">
                        <span className="text-muted-foreground">
                          Syncing... {jobStatus.progress} / {jobStatus.total}
                        </span>
                        <span className="text-muted-foreground">
                          {jobStatus.total > 0 ? Math.round((jobStatus.progress / jobStatus.total) * 100) : 0}%
                        </span>
                      </div>
                      <Progress
                        value={jobStatus.total > 0 ? (jobStatus.progress / jobStatus.total) * 100 : 0}
                        className="h-2"
                      />
                      {jobStatus.stats && (
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          {jobStatus.stats.tv !== undefined && <span>TV: {jobStatus.stats.tv}</span>}
                          {jobStatus.stats.movie !== undefined && <span>Movies: {jobStatus.stats.movie}</span>}
                          {jobStatus.stats.skipped !== undefined && <span>Skipped: {jobStatus.stats.skipped}</span>}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Completed notification */}
                  {completedJob && (
                    <div
                      className={`ml-16 p-2 rounded-lg text-sm ${
                        completedJob.status === 'completed'
                          ? 'bg-emerald-500/10 text-emerald-600'
                          : 'bg-red-500/10 text-red-600'
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        {completedJob.status === 'completed' ? (
                          <CheckCircle className="h-4 w-4" />
                        ) : (
                          <XCircle className="h-4 w-4" />
                        )}
                        <span>
                          {completedJob.status === 'completed'
                            ? `Sync complete! Added ${completedJob.stats?.tv || 0} TV, ${completedJob.stats?.movie || 0} movies`
                            : `Sync failed: ${completedJob.error || 'Unknown error'}`}
                        </span>
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            )
          })}
        </div>
      )}

      {/* Sync Result Toast - only show for immediate (non-background) sync results */}
      {syncSource.isSuccess && syncSource.data && syncSource.data.status !== 'processing' && (
        <div
          className={`fixed bottom-4 right-4 p-4 rounded-xl shadow-lg z-50 ${
            syncSource.data.status === 'success'
              ? 'bg-emerald-500/10 border border-emerald-500/20'
              : 'bg-red-500/10 border border-red-500/20'
          }`}
        >
          <div className="flex items-center gap-3">
            {syncSource.data.status === 'success' ? (
              <CheckCircle className="h-5 w-5 text-emerald-500" />
            ) : (
              <XCircle className="h-5 w-5 text-red-500" />
            )}
            <p className={syncSource.data.status === 'success' ? 'text-emerald-600' : 'text-red-600'}>
              {syncSource.data.message}
            </p>
          </div>
        </div>
      )}

      {/* Edit Dialog */}
      <Dialog open={!!editingSource} onOpenChange={(open) => !open && setEditingSource(null)}>
        <DialogContent className="glass border-border/50">
          <DialogHeader>
            <DialogTitle>Edit Source</DialogTitle>
            <DialogDescription>Update source settings</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="edit-name">Name</Label>
              <Input
                id="edit-name"
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="rounded-xl"
              />
            </div>

            <div className="space-y-3">
              <Label>Import Types</Label>

              <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30">
                <div className="flex items-center gap-2">
                  <Tv className="h-4 w-4 text-blue-500" />
                  <span>Live TV</span>
                </div>
                <Switch checked={editImportLive} onCheckedChange={setEditImportLive} />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30">
                <div className="flex items-center gap-2">
                  <Film className="h-4 w-4 text-emerald-500" />
                  <span>Movies</span>
                </div>
                <Switch checked={editImportVod} onCheckedChange={setEditImportVod} />
              </div>

              <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30">
                <div className="flex items-center gap-2">
                  <MonitorPlay className="h-4 w-4 text-primary" />
                  <span>Series</span>
                </div>
                <Switch checked={editImportSeries} onCheckedChange={setEditImportSeries} />
              </div>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setEditingSource(null)} disabled={updateSource.isPending}>
              Cancel
            </Button>
            <Button
              onClick={handleSaveEdit}
              disabled={updateSource.isPending}
              className="bg-gradient-to-r from-primary to-primary/80"
            >
              {updateSource.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle className="mr-2 h-4 w-4" />
              )}
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog open={!!deletingSourceId} onOpenChange={(open) => !open && setDeletingSourceId(null)}>
        <AlertDialogContent className="glass border-border/50">
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Source?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove the saved source configuration. Imported content will NOT be deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteSource.isPending}>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleteSource.isPending}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {deleteSource.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Trash2 className="mr-2 h-4 w-4" />
              )}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
