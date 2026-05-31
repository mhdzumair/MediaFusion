import { useMemo, useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import {
  useImdbDatasetConfig,
  useImdbDatasetStatus,
  useRunImdbDatasetImport,
  useUpdateImdbDatasetConfig,
} from '@/hooks'
import { useToast } from '@/hooks/use-toast'
import type { ImdbDatasetImportStateRow } from '@/lib/api/scrapers'
import { ApiRequestError } from '@/lib/api'

import { Database, Loader2, Play, RefreshCw, Save } from 'lucide-react'

function formatRows(value: number | null | undefined): string {
  if (value == null) return '—'
  return value.toLocaleString()
}

function phaseBadgeClass(phase: string): string {
  if (phase === 'complete') return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
  if (phase === 'error') return 'bg-red-500/10 text-red-500 border-red-500/30'
  if (phase === 'idle') return 'bg-muted text-muted-foreground border-border'
  return 'bg-blue-500/10 text-blue-500 border-blue-500/30'
}

export function ImdbDatasetImportPanel() {
  const { toast } = useToast()
  const configQuery = useImdbDatasetConfig()
  const liveStatusQuery = useImdbDatasetStatus(true)
  const updateConfig = useUpdateImdbDatasetConfig()
  const runImport = useRunImdbDatasetImport()

  const [localOverrides, setLocalOverrides] = useState<{
    enabled?: boolean
    schedule?: string
    includeAdult?: boolean
    selectedDatasets?: string[]
  }>({})
  const [runForce, setRunForce] = useState(false)
  const [runMergeOnly, setRunMergeOnly] = useState(false)

  const config = configQuery.data
  const liveStatus = liveStatusQuery.data
  const isRunning = !!liveStatus?.phase && !['idle', 'complete', 'error'].includes(liveStatus.phase)

  const enabled = localOverrides.enabled ?? config?.enabled ?? false
  const schedule = localOverrides.schedule ?? config?.schedule ?? '0 4 * * 0'
  const includeAdult = localOverrides.includeAdult ?? config?.include_adult ?? false
  const selectedDatasets = localOverrides.selectedDatasets ?? config?.datasets ?? config?.available_datasets ?? []

  const importStateByDataset = useMemo(() => {
    const map = new Map<string, ImdbDatasetImportStateRow>()
    for (const row of config?.import_state ?? []) {
      map.set(row.dataset, row)
    }
    return map
  }, [config?.import_state])

  const toggleDataset = (dataset: string, checked: boolean) => {
    setLocalOverrides((current) => {
      const base = current.selectedDatasets ?? config?.datasets ?? config?.available_datasets ?? []
      const next = checked
        ? base.includes(dataset)
          ? base
          : [...base, dataset]
        : base.filter((item) => item !== dataset)
      return { ...current, selectedDatasets: next }
    })
  }

  const handleSaveConfig = async () => {
    if (selectedDatasets.length === 0) {
      toast({
        title: 'Select at least one dataset',
        variant: 'destructive',
      })
      return
    }

    try {
      await updateConfig.mutateAsync({
        enabled,
        schedule,
        datasets: selectedDatasets,
        include_adult: includeAdult,
      })
      setLocalOverrides({})
      toast({ title: 'IMDb import settings saved' })
    } catch (error) {
      toast({
        title: 'Failed to save settings',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  const handleRunImport = async () => {
    if (selectedDatasets.length === 0) {
      toast({
        title: 'Select at least one dataset',
        variant: 'destructive',
      })
      return
    }

    try {
      await runImport.mutateAsync({
        datasets: selectedDatasets,
        force: runForce,
        merge_only: runMergeOnly,
        include_adult: includeAdult,
      })
      toast({
        title: 'IMDb import queued',
        description: runMergeOnly
          ? 'Merge-only run queued from existing staging tables.'
          : 'Background import job queued.',
      })
    } catch (error) {
      toast({
        title: 'Failed to queue import',
        description: error instanceof Error ? error.message : 'An error occurred',
        variant: 'destructive',
      })
    }
  }

  return (
    <Card className="glass border-border/50">
      <CardContent className="p-4 space-y-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded-xl bg-emerald-500/10">
              <Database className="h-4 w-4 text-emerald-500" />
            </div>
            <div>
              <p className="font-medium">IMDb Dataset Import</p>
              <p className="text-xs text-muted-foreground">{config ? config.base_url : 'Loading configuration...'}</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="rounded-lg"
              onClick={() => {
                configQuery.refetch()
                liveStatusQuery.refetch()
              }}
              disabled={configQuery.isFetching || liveStatusQuery.isFetching}
            >
              <RefreshCw className="mr-2 h-4 w-4" />
              Refresh
            </Button>
            <Button
              size="sm"
              className="rounded-lg"
              onClick={handleRunImport}
              disabled={runImport.isPending || configQuery.isLoading || isRunning}
            >
              {runImport.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Play className="mr-2 h-4 w-4" />
              )}
              Run Import
            </Button>
            <Badge className={phaseBadgeClass(liveStatus?.phase ?? 'idle')}>
              {isRunning ? 'Running' : (liveStatus?.phase ?? 'idle')}
            </Badge>
            <Badge
              className={
                config?.enabled
                  ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
                  : 'bg-red-500/10 text-red-500 border-red-500/30'
              }
            >
              {config?.enabled ? 'Cron On' : 'Cron Off'}
            </Badge>
          </div>
        </div>

        {configQuery.isLoading ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {[...Array(4)].map((_, index) => (
              <Skeleton key={index} className="h-14 rounded-xl" />
            ))}
          </div>
        ) : configQuery.isError ? (
          <div className="rounded-xl border border-primary/30 bg-primary/10 p-3 text-sm text-primary space-y-1">
            <p>Unable to load IMDb import configuration from the backend.</p>
            {configQuery.error instanceof ApiRequestError && (
              <p className="text-xs text-muted-foreground">
                {configQuery.error.message}
                {configQuery.error.status === 404
                  ? ' — restart the API server after pulling the latest backend changes.'
                  : null}
              </p>
            )}
          </div>
        ) : (
          <>
            {liveStatus && liveStatus.phase !== 'idle' && (
              <div className="rounded-xl border border-border/50 bg-muted/20 p-3 grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                <div>
                  <p className="text-xs text-muted-foreground">Phase</p>
                  <p className="font-medium mt-1">{liveStatus.phase}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Dataset</p>
                  <p className="font-medium mt-1">{liveStatus.dataset ?? '—'}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Rows Loaded</p>
                  <p className="font-medium mt-1">{formatRows(liveStatus.rows_loaded)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Rows Merged</p>
                  <p className="font-medium mt-1">{formatRows(liveStatus.rows_merged)}</p>
                </div>
                {liveStatus.message && (
                  <div className="col-span-full text-xs text-muted-foreground">{liveStatus.message}</div>
                )}
              </div>
            )}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              <div className="space-y-4 rounded-xl border border-border/50 bg-muted/20 p-4">
                <p className="text-sm font-medium">Scheduler Settings</p>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <Label htmlFor="imdb-cron-enabled">Enable weekly cron</Label>
                    <p className="text-xs text-muted-foreground">Runs automatically on the configured schedule.</p>
                  </div>
                  <Switch
                    id="imdb-cron-enabled"
                    checked={enabled}
                    onCheckedChange={(value) => setLocalOverrides((current) => ({ ...current, enabled: value }))}
                    aria-label="Enable IMDb import cron"
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="imdb-cron-schedule">Cron schedule</Label>
                  <Input
                    id="imdb-cron-schedule"
                    value={schedule}
                    onChange={(event) => setLocalOverrides((current) => ({ ...current, schedule: event.target.value }))}
                    placeholder="0 4 * * 0"
                    className="font-mono"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <Label htmlFor="imdb-include-adult">Include adult titles</Label>
                    <p className="text-xs text-muted-foreground">Applies to basics merge.</p>
                  </div>
                  <Switch
                    id="imdb-include-adult"
                    checked={includeAdult}
                    onCheckedChange={(value) => setLocalOverrides((current) => ({ ...current, includeAdult: value }))}
                    aria-label="Include adult titles"
                  />
                </div>
                <Button
                  size="sm"
                  variant="outline"
                  className="rounded-lg"
                  onClick={handleSaveConfig}
                  disabled={updateConfig.isPending}
                >
                  {updateConfig.isPending ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="mr-2 h-4 w-4" />
                  )}
                  Save Settings
                </Button>
              </div>

              <div className="space-y-4 rounded-xl border border-border/50 bg-muted/20 p-4">
                <p className="text-sm font-medium">Manual Run Options</p>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  {(config?.available_datasets ?? []).map((dataset) => (
                    <label key={dataset} className="flex items-center gap-2 rounded-lg border border-border/40 p-2">
                      <Checkbox
                        checked={selectedDatasets.includes(dataset)}
                        onCheckedChange={(checked) => toggleDataset(dataset, checked === true)}
                      />
                      <span className="text-sm capitalize">{dataset}</span>
                    </label>
                  ))}
                </div>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <Label htmlFor="imdb-run-force">Force re-download</Label>
                    <p className="text-xs text-muted-foreground">Ignore 304 Not Modified responses.</p>
                  </div>
                  <Switch
                    id="imdb-run-force"
                    checked={runForce}
                    onCheckedChange={setRunForce}
                    aria-label="Force re-download"
                  />
                </div>
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <Label htmlFor="imdb-run-merge-only">Merge only</Label>
                    <p className="text-xs text-muted-foreground">Skip download/COPY and merge staging tables.</p>
                  </div>
                  <Switch
                    id="imdb-run-merge-only"
                    checked={runMergeOnly}
                    onCheckedChange={setRunMergeOnly}
                    aria-label="Merge only"
                  />
                </div>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
              {(config?.available_datasets ?? []).map((dataset) => {
                const row = importStateByDataset.get(dataset)
                return (
                  <div key={dataset} className="rounded-xl border border-border/50 bg-muted/20 p-3">
                    <p className="text-xs text-muted-foreground capitalize">{dataset}</p>
                    <p className="text-sm mt-1">{formatRows(row?.rows_loaded)} rows</p>
                    <p className="text-[11px] text-muted-foreground mt-1 truncate">
                      {row?.last_run_at ? new Date(row.last_run_at).toLocaleString() : 'Never'}
                    </p>
                  </div>
                )
              })}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
