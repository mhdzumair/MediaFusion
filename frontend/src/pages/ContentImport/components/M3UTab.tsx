import { useState, useMemo, useCallback } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
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
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  FileVideo,
  Link as LinkIcon,
  Loader2,
  ArrowRight,
  Info,
  Tv,
  Film,
  MonitorPlay,
  HelpCircle,
  Globe,
  Lock,
  Search,
  X,
  FileInput,
  CheckCircle,
} from 'lucide-react'
import { useAnalyzeM3U, useImportM3U, useImportJobStatus } from '@/hooks'
import type {
  M3UAnalyzeResponse,
  M3UChannelPreview,
  M3UContentType,
  M3UImportOverride,
  IPTVImportSettings,
} from '@/lib/api'

const CONTENT_TYPE_ICONS: Record<M3UContentType, React.ElementType> = {
  tv: Tv,
  movie: Film,
  series: MonitorPlay,
  unknown: HelpCircle,
}

const CONTENT_TYPE_LABELS: Record<M3UContentType, string> = {
  tv: 'TV Channel',
  movie: 'Movie',
  series: 'Series',
  unknown: 'Unknown',
}

interface M3UTabProps {
  onSuccess: (message: string) => void
  onError: (message: string) => void
  iptvSettings?: IPTVImportSettings
}

export function M3UTab({ onSuccess, onError, iptvSettings }: M3UTabProps) {
  const [m3uUrl, setM3uUrl] = useState('')
  const [analysis, setAnalysis] = useState<M3UAnalyzeResponse | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  // Default to private if public sharing is not allowed
  const [isPublic, setIsPublic] = useState(iptvSettings?.allow_public_sharing ?? false)
  const [overrides, setOverrides] = useState<Map<number, M3UContentType>>(new Map())
  const [typeFilter, setTypeFilter] = useState<M3UContentType | 'all'>('all')
  const [search, setSearch] = useState('')
  const [saveSource, setSaveSource] = useState(true)
  const [sourceName, setSourceName] = useState('')
  const [importJobId, setImportJobId] = useState<string | null>(null)

  const analyzeM3U = useAnalyzeM3U()
  const importM3U = useImportM3U()

  // Poll for import job status
  const { data: jobStatus } = useImportJobStatus(importJobId, {
    onComplete: (status) => {
      const stats = status.stats || {}
      const total = (stats.tv || 0) + (stats.movie || 0) + (stats.series || 0)
      onSuccess(
        `Successfully imported ${total} items (${stats.tv || 0} TV, ${stats.movie || 0} movies, ${stats.series || 0} series)`,
      )
      setImportJobId(null)
      setM3uUrl('')
      setAnalysis(null)
      setOverrides(new Map())
      setSourceName('')
    },
    onError: (status) => {
      onError(`Import failed: ${status.error || 'Unknown error'}`)
      setImportJobId(null)
    },
  })

  const handleAnalyze = async () => {
    if (!m3uUrl.trim()) return

    try {
      const result = await analyzeM3U.mutateAsync({ m3u_url: m3uUrl })
      if (result.status === 'success') {
        setAnalysis(result)
        setPreviewOpen(true)
        setOverrides(new Map())
        setTypeFilter('all')
        setSearch('')
      } else {
        onError(result.error || 'Failed to analyze M3U playlist')
      }
    } catch {
      onError('M3U analysis failed. Please check the URL.')
    }
  }

  const handleImport = async () => {
    if (!analysis) return

    const overrideList: M3UImportOverride[] = Array.from(overrides.entries()).map(([index, type]) => ({
      index,
      type,
    }))

    try {
      const result = await importM3U.mutateAsync({
        redis_key: analysis.redis_key,
        is_public: isPublic,
        overrides: overrideList.length > 0 ? overrideList : undefined,
        save_source: saveSource,
        source_name: sourceName || undefined,
      })

      // Check if it's a background job
      if (result.status === 'processing' && result.details?.job_id) {
        setImportJobId(result.details.job_id)
        setPreviewOpen(false)
        // Don't clear other state yet - wait for job completion
      } else if (result.status === 'success') {
        onSuccess('M3U playlist imported successfully!')
        setM3uUrl('')
        setAnalysis(null)
        setPreviewOpen(false)
        setOverrides(new Map())
        setSourceName('')
      } else {
        onError(result.message || 'Import failed')
      }
    } catch {
      onError('M3U import failed. Please try again.')
    }
  }

  const handleTypeOverride = (index: number, type: M3UContentType) => {
    setOverrides((prev) => {
      const newMap = new Map(prev)
      newMap.set(index, type)
      return newMap
    })
  }

  const getEffectiveType = useCallback(
    (channel: M3UChannelPreview): M3UContentType => {
      return overrides.get(channel.index) ?? channel.detected_type
    },
    [overrides],
  )

  // Compute dynamic counts based on overrides
  const dynamicCounts = useMemo(() => {
    if (!analysis) return { tv: 0, movie: 0, series: 0, unknown: 0 }

    const counts: Record<M3UContentType, number> = { tv: 0, movie: 0, series: 0, unknown: 0 }
    analysis.channels.forEach((channel) => {
      const effectiveType = getEffectiveType(channel)
      counts[effectiveType]++
    })
    return counts
  }, [analysis, getEffectiveType])

  const filteredChannels = useMemo(() => {
    if (!analysis) return []

    return analysis.channels.filter((channel) => {
      if (typeFilter !== 'all') {
        const effectiveType = getEffectiveType(channel)
        if (effectiveType !== typeFilter) return false
      }

      if (search.trim()) {
        const searchLower = search.toLowerCase()
        const nameMatches = channel.name.toLowerCase().includes(searchLower)
        const titleMatches = channel.parsed_title?.toLowerCase().includes(searchLower)
        const matchedMatches = channel.matched_media?.title.toLowerCase().includes(searchLower)
        if (!nameMatches && !titleMatches && !matchedMatches) return false
      }

      return true
    })
  }, [analysis, typeFilter, search, getEffectiveType])

  const isAnalyzing = analyzeM3U.isPending
  const isImporting = importM3U.isPending || !!importJobId

  // Calculate progress percentage
  const progressPercent = jobStatus?.total ? Math.round((jobStatus.progress / jobStatus.total) * 100) : 0

  return (
    <>
      <Card className="glass border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileVideo className="h-5 w-5 text-primary" />
            Import M3U Playlist
          </CardTitle>
          <CardDescription>Add an M3U playlist URL to import TV channels, movies, and series</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="m3u">Playlist URL</Label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  id="m3u"
                  placeholder="https://example.com/playlist.m3u"
                  value={m3uUrl}
                  onChange={(e) => setM3uUrl(e.target.value)}
                  className="pl-10 rounded-xl"
                  disabled={isImporting}
                />
              </div>
              <Button
                onClick={handleAnalyze}
                disabled={!m3uUrl.trim() || isAnalyzing || isImporting}
                className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
              >
                {isAnalyzing ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <ArrowRight className="mr-2 h-4 w-4" />
                )}
                Analyze
              </Button>
            </div>
          </div>

          {/* Background Import Progress */}
          {importJobId && jobStatus && (
            <div className="p-4 rounded-xl bg-primary/10 border border-primary/20 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin text-primary" />
                  <span className="font-medium">Importing in background...</span>
                </div>
                <span className="text-sm text-muted-foreground">
                  {jobStatus.progress} / {jobStatus.total}
                </span>
              </div>
              <Progress value={progressPercent} className="h-2" />
              <div className="flex gap-4 text-sm text-muted-foreground">
                <span>TV: {jobStatus.stats?.tv || 0}</span>
                <span>Movies: {jobStatus.stats?.movie || 0}</span>
                <span>Series: {jobStatus.stats?.series || 0}</span>
                {(jobStatus.stats?.skipped || 0) > 0 && <span>Skipped: {jobStatus.stats.skipped}</span>}
                {(jobStatus.stats?.failed || 0) > 0 && (
                  <span className="text-red-500">Failed: {jobStatus.stats.failed}</span>
                )}
              </div>
            </div>
          )}

          <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
            <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
            <div className="text-sm text-muted-foreground space-y-1">
              <p>The M3U playlist will be analyzed to detect content types (TV, Movies, Series).</p>
              <p>You&apos;ll be able to review and adjust the detected types before importing.</p>
              <p>Supported formats: M3U, M3U8</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Preview Dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="glass border-border/50 sm:max-w-[800px] max-h-[90vh] flex flex-col">
          <DialogHeader className="flex-shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <FileVideo className="h-5 w-5 text-primary" />
              M3U Playlist Preview
            </DialogTitle>
            <DialogDescription>Review detected content types and choose visibility before importing</DialogDescription>
          </DialogHeader>

          {analysis && (
            <ScrollArea className="flex-1 pr-4">
              <div className="space-y-4">
                {/* Summary - Clickable filters with dynamic counts */}
                <div className="flex flex-wrap gap-2 p-3 rounded-xl bg-muted/50">
                  <Button
                    variant={typeFilter === 'all' ? 'default' : 'outline'}
                    size="sm"
                    className="h-7"
                    onClick={() => setTypeFilter('all')}
                  >
                    All
                    <Badge variant="secondary" className="ml-1.5 text-xs">
                      {analysis.total_count}
                    </Badge>
                  </Button>
                  {(['tv', 'movie', 'series', 'unknown'] as M3UContentType[]).map((type) => {
                    const Icon = CONTENT_TYPE_ICONS[type]
                    const count = dynamicCounts[type]
                    return count > 0 ? (
                      <Button
                        key={type}
                        variant={typeFilter === type ? 'default' : 'outline'}
                        size="sm"
                        className="h-7"
                        onClick={() => setTypeFilter(type)}
                      >
                        <Icon className="h-3.5 w-3.5 mr-1" />
                        {CONTENT_TYPE_LABELS[type]}
                        <Badge variant="secondary" className="ml-1.5 text-xs">
                          {count}
                        </Badge>
                      </Button>
                    ) : null
                  })}
                </div>

                {/* Search */}
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search by name..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    className="pl-10 pr-10 rounded-xl h-9"
                  />
                  {search && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="absolute right-1 top-1/2 -translate-y-1/2 h-7 w-7 p-0"
                      onClick={() => setSearch('')}
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  )}
                </div>

                {/* Import Settings - Compact */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium">Import Settings</Label>

                  <div className="grid grid-cols-2 gap-2">
                    {/* Visibility */}
                    <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30 border border-border/50">
                      <div className="flex items-center gap-2">
                        {isPublic ? (
                          <Globe className="h-4 w-4 text-emerald-500" />
                        ) : (
                          <Lock className="h-4 w-4 text-primary" />
                        )}
                        <div>
                          <span className="text-sm font-medium">{isPublic ? 'Public' : 'Private'}</span>
                          {!iptvSettings?.allow_public_sharing && (
                            <p className="text-xs text-muted-foreground">Public sharing disabled by server</p>
                          )}
                        </div>
                      </div>
                      <Switch
                        checked={isPublic}
                        onCheckedChange={setIsPublic}
                        disabled={!iptvSettings?.allow_public_sharing}
                      />
                    </div>

                    {/* Save Source */}
                    <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30 border border-border/50">
                      <div className="flex items-center gap-2">
                        <FileInput className="h-4 w-4 text-primary" />
                        <span className="text-sm font-medium">Save for Re-sync</span>
                      </div>
                      <Switch checked={saveSource} onCheckedChange={setSaveSource} />
                    </div>
                  </div>

                  {/* Source Name */}
                  {saveSource && (
                    <Input
                      placeholder="Source Name (optional)"
                      value={sourceName}
                      onChange={(e) => setSourceName(e.target.value)}
                      className="rounded-xl h-9"
                    />
                  )}
                </div>

                {/* Channel List */}
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <Label className="text-sm font-medium">
                      {filteredChannels.length === analysis.channels.length ? (
                        <>
                          Preview ({analysis.channels.length} of {analysis.total_count})
                        </>
                      ) : (
                        <>
                          Showing {filteredChannels.length} of {analysis.channels.length}
                        </>
                      )}
                    </Label>
                    {(typeFilter !== 'all' || search) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 text-xs"
                        onClick={() => {
                          setTypeFilter('all')
                          setSearch('')
                        }}
                      >
                        Clear filters
                      </Button>
                    )}
                  </div>
                  <ScrollArea className="max-h-[200px] rounded-xl border border-border/50">
                    <div className="divide-y divide-border/50">
                      {filteredChannels.length === 0 ? (
                        <div className="flex items-center justify-center h-24 text-muted-foreground text-sm">
                          No items match your filters
                        </div>
                      ) : (
                        filteredChannels.map((channel) => {
                          const effectiveType = getEffectiveType(channel)
                          const Icon = CONTENT_TYPE_ICONS[effectiveType]
                          const isOverridden = overrides.has(channel.index)

                          return (
                            <div
                              key={channel.index}
                              className={`flex items-center gap-3 p-2 ${isOverridden ? 'bg-primary/5' : ''}`}
                            >
                              {/* Logo/Icon */}
                              <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-muted/50 flex items-center justify-center overflow-hidden">
                                {channel.logo ? (
                                  <img
                                    src={channel.logo}
                                    alt={channel.name}
                                    className="w-full h-full object-cover"
                                    onError={(e) => {
                                      e.currentTarget.style.display = 'none'
                                    }}
                                  />
                                ) : (
                                  <Icon className="h-5 w-5 text-muted-foreground" />
                                )}
                              </div>

                              {/* Info */}
                              <div className="flex-1 min-w-0">
                                <p className="font-medium text-sm truncate">{channel.name}</p>
                                {channel.matched_media && (
                                  <span className="text-xs text-emerald-600 dark:text-emerald-400">
                                    ✓ {channel.matched_media.title}
                                  </span>
                                )}
                              </div>

                              {/* Type Selector */}
                              <Select
                                value={effectiveType}
                                onValueChange={(value) => handleTypeOverride(channel.index, value as M3UContentType)}
                              >
                                <SelectTrigger className="w-[130px] h-8 text-xs">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  <SelectItem value="tv">
                                    <span className="flex items-center gap-2">
                                      <Tv className="h-3.5 w-3.5" /> TV Channel
                                    </span>
                                  </SelectItem>
                                  <SelectItem value="movie">
                                    <span className="flex items-center gap-2">
                                      <Film className="h-3.5 w-3.5" /> Movie
                                    </span>
                                  </SelectItem>
                                  <SelectItem value="series">
                                    <span className="flex items-center gap-2">
                                      <MonitorPlay className="h-3.5 w-3.5" /> Series
                                    </span>
                                  </SelectItem>
                                  <SelectItem value="unknown">
                                    <span className="flex items-center gap-2">
                                      <HelpCircle className="h-3.5 w-3.5" /> Skip
                                    </span>
                                  </SelectItem>
                                </SelectContent>
                              </Select>
                            </div>
                          )
                        })
                      )}
                    </div>
                  </ScrollArea>
                </div>
              </div>
            </ScrollArea>
          )}

          <DialogFooter className="flex-shrink-0 border-t border-border/50 pt-4 mt-4">
            <Button variant="outline" onClick={() => setPreviewOpen(false)} disabled={isImporting}>
              Cancel
            </Button>
            <Button
              onClick={handleImport}
              disabled={isImporting}
              className="bg-gradient-to-r from-primary to-primary/80"
            >
              {importM3U.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <CheckCircle className="mr-2 h-4 w-4" />
                  Import {analysis?.total_count ?? 0} Items
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
