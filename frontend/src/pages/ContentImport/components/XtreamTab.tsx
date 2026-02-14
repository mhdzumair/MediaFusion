import { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Progress } from '@/components/ui/progress'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Tv, Film, MonitorPlay, Globe, Lock, Loader2, ArrowRight, Info, FileInput, CheckCircle } from 'lucide-react'
import { useAnalyzeXtream, useImportXtream, useImportJobStatus } from '@/hooks'
import type { XtreamAnalyzeResponse, IPTVImportSettings } from '@/lib/api'

interface XtreamTabProps {
  onSuccess: (message: string) => void
  onError: (message: string) => void
  iptvSettings?: IPTVImportSettings
}

export function XtreamTab({ onSuccess, onError, iptvSettings }: XtreamTabProps) {
  const [serverUrl, setServerUrl] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [analysis, setAnalysis] = useState<XtreamAnalyzeResponse | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  // Default to private if public sharing is not allowed
  const [isPublic, setIsPublic] = useState(iptvSettings?.allow_public_sharing ?? false)
  const [saveSource, setSaveSource] = useState(true)
  const [sourceName, setSourceName] = useState('')
  const [importLive, setImportLive] = useState(true)
  const [importVod, setImportVod] = useState(true)
  const [importSeries, setImportSeries] = useState(true)
  const [selectedLiveCats, setSelectedLiveCats] = useState<Set<string>>(new Set())
  const [selectedVodCats, setSelectedVodCats] = useState<Set<string>>(new Set())
  const [selectedSeriesCats, setSelectedSeriesCats] = useState<Set<string>>(new Set())
  const [importJobId, setImportJobId] = useState<string | null>(null)

  const analyzeXtream = useAnalyzeXtream()
  const importXtream = useImportXtream()

  // Poll for import job status
  const { data: jobStatus } = useImportJobStatus(importJobId, {
    onComplete: (status) => {
      const stats = status.stats || {}
      const total = (stats.tv || 0) + (stats.movie || 0) + (stats.series || 0)
      onSuccess(
        `Successfully imported ${total} items (${stats.tv || 0} TV, ${stats.movie || 0} movies, ${stats.series || 0} series)`,
      )
      setImportJobId(null)
      setServerUrl('')
      setUsername('')
      setPassword('')
      setAnalysis(null)
      setSourceName('')
    },
    onError: (status) => {
      onError(`Import failed: ${status.error || 'Unknown error'}`)
      setImportJobId(null)
    },
  })

  const handleAnalyze = async () => {
    if (!serverUrl.trim() || !username.trim() || !password.trim()) return

    try {
      const result = await analyzeXtream.mutateAsync({
        server_url: serverUrl,
        username: username,
        password: password,
      })
      if (result.status === 'success') {
        setAnalysis(result)
        setPreviewOpen(true)
        // Select all categories by default
        setSelectedLiveCats(new Set(result.live_categories.map((c) => c.id)))
        setSelectedVodCats(new Set(result.vod_categories.map((c) => c.id)))
        setSelectedSeriesCats(new Set(result.series_categories.map((c) => c.id)))
      } else {
        onError(result.error || 'Failed to connect to Xtream server')
      }
    } catch {
      onError('Failed to connect to Xtream server. Please check your credentials.')
    }
  }

  const handleImport = async () => {
    if (!analysis) return

    try {
      let defaultSourceName = sourceName
      if (!defaultSourceName) {
        try {
          defaultSourceName = `Xtream - ${new URL(serverUrl).hostname}`
        } catch {
          defaultSourceName = 'Xtream Server'
        }
      }

      const result = await importXtream.mutateAsync({
        redis_key: analysis.redis_key,
        source_name: defaultSourceName,
        save_source: saveSource,
        is_public: isPublic,
        import_live: importLive,
        import_vod: importVod,
        import_series: importSeries,
        live_category_ids: importLive ? Array.from(selectedLiveCats) : undefined,
        vod_category_ids: importVod ? Array.from(selectedVodCats) : undefined,
        series_category_ids: importSeries ? Array.from(selectedSeriesCats) : undefined,
      })

      // Check if it's a background job
      if (result.status === 'processing' && result.details?.job_id) {
        setImportJobId(result.details.job_id)
        setPreviewOpen(false)
      } else if (result.status === 'success') {
        onSuccess('Xtream content imported successfully!')
        setServerUrl('')
        setUsername('')
        setPassword('')
        setAnalysis(null)
        setPreviewOpen(false)
        setSourceName('')
      } else {
        onError(result.message || 'Import failed')
      }
    } catch {
      onError('Xtream import failed. Please try again.')
    }
  }

  const toggleCategory = (type: 'live' | 'vod' | 'series', categoryId: string) => {
    if (type === 'live') {
      setSelectedLiveCats((prev) => {
        const newSet = new Set(prev)
        if (newSet.has(categoryId)) {
          newSet.delete(categoryId)
        } else {
          newSet.add(categoryId)
        }
        return newSet
      })
    } else if (type === 'vod') {
      setSelectedVodCats((prev) => {
        const newSet = new Set(prev)
        if (newSet.has(categoryId)) {
          newSet.delete(categoryId)
        } else {
          newSet.add(categoryId)
        }
        return newSet
      })
    } else {
      setSelectedSeriesCats((prev) => {
        const newSet = new Set(prev)
        if (newSet.has(categoryId)) {
          newSet.delete(categoryId)
        } else {
          newSet.add(categoryId)
        }
        return newSet
      })
    }
  }

  const isAnalyzing = analyzeXtream.isPending
  const isImporting = importXtream.isPending || !!importJobId

  // Format expiry date
  const formatExpiry = (exp: string | undefined) => {
    if (!exp) return 'Never'
    const timestamp = parseInt(exp)
    if (isNaN(timestamp)) return exp
    return new Date(timestamp * 1000).toLocaleDateString()
  }

  // Calculate progress percentage
  const progressPercent = jobStatus?.total ? Math.round((jobStatus.progress / jobStatus.total) * 100) : 0

  return (
    <>
      <Card className="glass border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Tv className="h-5 w-5 text-primary" />
            Import from Xtream Codes
          </CardTitle>
          <CardDescription>Connect to an Xtream Codes server to import Live TV, Movies, and Series</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="xtream-server">Server URL</Label>
            <div className="relative">
              <Globe className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
              <Input
                id="xtream-server"
                placeholder="http://example.com:8080"
                value={serverUrl}
                onChange={(e) => setServerUrl(e.target.value)}
                className="pl-10 rounded-xl"
                disabled={isImporting}
              />
            </div>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label htmlFor="xtream-username">Username</Label>
              <Input
                id="xtream-username"
                placeholder="Username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="rounded-xl"
                disabled={isImporting}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="xtream-password">Password</Label>
              <Input
                id="xtream-password"
                type="password"
                placeholder="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded-xl"
                disabled={isImporting}
              />
            </div>
          </div>
          <Button
            onClick={handleAnalyze}
            disabled={!serverUrl.trim() || !username.trim() || !password.trim() || isAnalyzing || isImporting}
            className="w-full rounded-xl bg-gradient-to-r from-primary to-primary/80"
          >
            {isAnalyzing ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ArrowRight className="mr-2 h-4 w-4" />}
            Connect & Analyze
          </Button>

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
              <p>Enter your Xtream Codes credentials to connect to the server.</p>
              <p>You&apos;ll be able to select which categories to import.</p>
              <p>Credentials can be saved (encrypted) for future re-sync.</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Preview Dialog */}
      <Dialog open={previewOpen} onOpenChange={setPreviewOpen}>
        <DialogContent className="glass border-border/50 sm:max-w-[700px] max-h-[90vh] flex flex-col">
          <DialogHeader className="flex-shrink-0">
            <DialogTitle className="flex items-center gap-2">
              <Tv className="h-5 w-5 text-primary" />
              Xtream Codes Import
            </DialogTitle>
            <DialogDescription>Select content to import from the Xtream server</DialogDescription>
          </DialogHeader>

          {analysis && (
            <ScrollArea className="flex-1 overflow-y-auto pr-4">
              <div className="space-y-4">
                {/* Account Info */}
                {analysis.account_info && (
                  <div className="grid grid-cols-3 gap-3 p-3 rounded-xl bg-muted/30 border border-border/50">
                    <div className="text-center">
                      <p className="text-xs text-muted-foreground">Status</p>
                      <p
                        className={`text-sm font-medium ${analysis.account_info.status === 'Active' ? 'text-emerald-500' : 'text-primary'}`}
                      >
                        {analysis.account_info.status}
                      </p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-muted-foreground">Expires</p>
                      <p className="text-sm font-medium">{formatExpiry(analysis.account_info.exp_date)}</p>
                    </div>
                    <div className="text-center">
                      <p className="text-xs text-muted-foreground">Connections</p>
                      <p className="text-sm font-medium">
                        {analysis.account_info.active_cons}/{analysis.account_info.max_connections}
                      </p>
                    </div>
                  </div>
                )}

                {/* Summary */}
                <div className="flex flex-wrap gap-2 p-3 rounded-xl bg-muted/50">
                  <Badge variant="outline" className="gap-1">
                    <Tv className="h-3 w-3" />
                    {analysis.summary.live || 0} Live
                  </Badge>
                  <Badge variant="outline" className="gap-1">
                    <Film className="h-3 w-3" />
                    {analysis.summary.vod || 0} Movies
                  </Badge>
                  <Badge variant="outline" className="gap-1">
                    <MonitorPlay className="h-3 w-3" />
                    {analysis.summary.series || 0} Series
                  </Badge>
                </div>

                {/* Content Types */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium">Content Types</Label>

                  {/* Live TV */}
                  <div className="p-3 rounded-xl bg-muted/30 border border-border/50">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Tv className="h-4 w-4 text-blue-500" />
                        <div>
                          <p className="text-sm font-medium">Live TV</p>
                          <p className="text-xs text-muted-foreground">
                            {analysis.live_categories.length} categories, {analysis.summary.live || 0} channels
                          </p>
                        </div>
                      </div>
                      <Switch checked={importLive} onCheckedChange={setImportLive} />
                    </div>
                    {importLive && analysis.live_categories.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-border/50 max-h-[80px] overflow-y-auto">
                        {analysis.live_categories.slice(0, 15).map((cat) => (
                          <Badge
                            key={cat.id}
                            variant={selectedLiveCats.has(cat.id) ? 'default' : 'outline'}
                            className="cursor-pointer text-xs"
                            onClick={() => toggleCategory('live', cat.id)}
                          >
                            {cat.name} ({cat.count})
                          </Badge>
                        ))}
                        {analysis.live_categories.length > 15 && (
                          <Badge variant="secondary" className="text-xs">
                            +{analysis.live_categories.length - 15} more
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>

                  {/* VOD */}
                  <div className="p-3 rounded-xl bg-muted/30 border border-border/50">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Film className="h-4 w-4 text-emerald-500" />
                        <div>
                          <p className="text-sm font-medium">Movies (VOD)</p>
                          <p className="text-xs text-muted-foreground">
                            {analysis.vod_categories.length} categories, {analysis.summary.vod || 0} movies
                          </p>
                        </div>
                      </div>
                      <Switch checked={importVod} onCheckedChange={setImportVod} />
                    </div>
                    {importVod && analysis.vod_categories.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-border/50 max-h-[80px] overflow-y-auto">
                        {analysis.vod_categories.slice(0, 15).map((cat) => (
                          <Badge
                            key={cat.id}
                            variant={selectedVodCats.has(cat.id) ? 'default' : 'outline'}
                            className="cursor-pointer text-xs"
                            onClick={() => toggleCategory('vod', cat.id)}
                          >
                            {cat.name} ({cat.count})
                          </Badge>
                        ))}
                        {analysis.vod_categories.length > 15 && (
                          <Badge variant="secondary" className="text-xs">
                            +{analysis.vod_categories.length - 15} more
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>

                  {/* Series */}
                  <div className="p-3 rounded-xl bg-muted/30 border border-border/50">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <MonitorPlay className="h-4 w-4 text-primary" />
                        <div>
                          <p className="text-sm font-medium">Series</p>
                          <p className="text-xs text-muted-foreground">
                            {analysis.series_categories.length} categories, {analysis.summary.series || 0} series
                          </p>
                        </div>
                      </div>
                      <Switch checked={importSeries} onCheckedChange={setImportSeries} />
                    </div>
                    {importSeries && analysis.series_categories.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-2 pt-2 border-t border-border/50 max-h-[80px] overflow-y-auto">
                        {analysis.series_categories.slice(0, 15).map((cat) => (
                          <Badge
                            key={cat.id}
                            variant={selectedSeriesCats.has(cat.id) ? 'default' : 'outline'}
                            className="cursor-pointer text-xs"
                            onClick={() => toggleCategory('series', cat.id)}
                          >
                            {cat.name} ({cat.count})
                          </Badge>
                        ))}
                        {analysis.series_categories.length > 15 && (
                          <Badge variant="secondary" className="text-xs">
                            +{analysis.series_categories.length - 15} more
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>
                </div>

                {/* Settings - Compact */}
                <div className="space-y-2">
                  <Label className="text-sm font-medium">Settings</Label>

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
                        <span className="text-sm font-medium">Save Credentials</span>
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
              </div>
            </ScrollArea>
          )}

          <DialogFooter className="flex-shrink-0 border-t border-border/50 pt-4 mt-4">
            <Button variant="outline" onClick={() => setPreviewOpen(false)} disabled={isImporting}>
              Cancel
            </Button>
            <Button
              onClick={handleImport}
              disabled={isImporting || (!importLive && !importVod && !importSeries)}
              className="bg-gradient-to-r from-primary to-primary/80"
            >
              {importXtream.isPending ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Starting...
                </>
              ) : (
                <>
                  <CheckCircle className="mr-2 h-4 w-4" />
                  Import Selected
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
