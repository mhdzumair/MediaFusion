import { useState, useMemo, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Progress } from '@/components/ui/progress'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  Download,
  Loader2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Film,
  Tv,
  HardDrive,
  FileVideo,
  Edit3,
  X,
  Save,
  Settings2,
  CloudOff,
  Settings,
} from 'lucide-react'
import { useWatchlistProviders, useProfiles, useMissingTorrents, useImportTorrents } from '@/hooks'
import type { MissingTorrentItem, ImportResultItem } from '@/lib/api/watchlist'
import { DEBRID_SERVICE_DISPLAY_NAMES, type WatchlistProviderInfo } from '@/lib/api'
import { cn } from '@/lib/utils'
import { AdvancedImportDialog } from '@/components/watchlist/AdvancedImportDialog'

// Providers that support import functionality
const IMPORT_SUPPORTED_PROVIDERS = new Set([
  'realdebrid',
  'alldebrid',
  'torbox',
  'debridlink',
  'premiumize',
  'offcloud',
  'seedr',
  'pikpak',
])

interface TorrentEdit {
  title?: string
  year?: number
  type?: 'movie' | 'series'
}

function hasResolvedExternalIds(torrent: MissingTorrentItem): boolean {
  return Boolean(torrent.external_ids?.imdb || torrent.external_ids?.tmdb || torrent.external_ids?.tvdb)
}

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
}

function TorrentItem({
  torrent,
  selected,
  onSelect,
  disabled,
  isEditing,
  onEditClick,
  edit,
}: {
  torrent: MissingTorrentItem
  selected: boolean
  onSelect: (selected: boolean) => void
  disabled?: boolean
  isEditing?: boolean
  onEditClick: () => void
  edit?: TorrentEdit
}) {
  const videoFiles = useMemo(() => {
    const videoExtensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v']
    return torrent.files.filter((f) => videoExtensions.some((ext) => f.path.toLowerCase().endsWith(ext)))
  }, [torrent.files])

  const displayTitle = edit?.title || torrent.parsed_title
  const displayYear = edit?.year || torrent.parsed_year
  const displayType = edit?.type || torrent.parsed_type
  const hasEdits = edit && (edit.title || edit.year || edit.type)
  const externalIdPairs = useMemo(() => {
    if (!torrent.external_ids) return []
    return (
      [
        ['IMDb', torrent.external_ids.imdb],
        ['TMDB', torrent.external_ids.tmdb],
        ['TVDB', torrent.external_ids.tvdb],
      ] as const
    ).filter(([, value]) => Boolean(value))
  }, [torrent.external_ids])

  return (
    <div
      className={cn(
        'flex items-start gap-3 p-3 rounded-lg border transition-colors',
        selected ? 'border-primary bg-primary/5' : 'border-border hover:border-border/80',
        isEditing && 'ring-2 ring-primary',
        disabled && 'opacity-50 cursor-not-allowed',
      )}
    >
      <Checkbox checked={selected} onCheckedChange={onSelect} disabled={disabled} className="mt-1" />

      <Button
        variant="outline"
        size="icon"
        className="h-8 w-8 flex-shrink-0"
        onClick={(e) => {
          e.stopPropagation()
          onEditClick()
        }}
        disabled={disabled}
        title="Edit metadata"
      >
        <Edit3 className="h-4 w-4" />
      </Button>

      <div className="flex-1 min-w-0 space-y-1.5">
        <div className="flex items-start gap-2">
          {displayType === 'series' ? (
            <Tv className="h-4 w-4 text-blue-500 mt-0.5 flex-shrink-0" />
          ) : (
            <Film className="h-4 w-4 text-purple-500 mt-0.5 flex-shrink-0" />
          )}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium truncate" title={torrent.name}>
              {torrent.name}
            </p>
            {displayTitle && (
              <p className="text-xs text-muted-foreground">
                Detected:{' '}
                <span className={cn('text-foreground', hasEdits && 'text-primary font-medium')}>{displayTitle}</span>
                {displayYear && <span className={hasEdits ? 'text-primary' : ''}> ({displayYear})</span>}
                {hasEdits && (
                  <Badge variant="outline" className="ml-2 text-[10px] px-1 py-0 text-primary">
                    Edited
                  </Badge>
                )}
              </p>
            )}
            <div className="flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
              <span>External IDs:</span>
              {externalIdPairs.length > 0 ? (
                externalIdPairs.map(([label, value]) => (
                  <Badge key={label} variant="outline" className="font-mono text-[10px] px-1 py-0">
                    {label}:{value}
                  </Badge>
                ))
              ) : (
                <span className="italic">Not matched</span>
              )}
              {externalIdPairs.length > 0 && torrent.matched_title && (
                <span className="truncate max-w-full" title={torrent.matched_title}>
                  ({torrent.matched_title})
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <HardDrive className="h-3 w-3" />
            {formatBytes(torrent.size)}
          </span>
          <span className="flex items-center gap-1">
            <FileVideo className="h-3 w-3" />
            {videoFiles.length} video{videoFiles.length !== 1 ? 's' : ''}
          </span>
          {displayType && (
            <Badge
              variant="outline"
              className={cn('text-[10px] px-1.5 py-0', hasEdits && 'border-primary text-primary')}
            >
              {displayType}
            </Badge>
          )}
        </div>
      </div>
    </div>
  )
}

function EditPanel({
  torrent,
  edit,
  onSave,
  onCancel,
  onAdvancedImport,
}: {
  torrent: MissingTorrentItem
  edit?: TorrentEdit
  onSave: (edit: TorrentEdit) => void
  onCancel: () => void
  onAdvancedImport: () => void
}) {
  const [title, setTitle] = useState(edit?.title || torrent.parsed_title || '')
  const [year, setYear] = useState(edit?.year?.toString() || torrent.parsed_year?.toString() || '')
  const [type, setType] = useState<'movie' | 'series'>(edit?.type || torrent.parsed_type || 'movie')

  const handleSave = () => {
    onSave({
      title: title || undefined,
      year: year ? parseInt(year, 10) : undefined,
      type,
    })
  }

  return (
    <div className="p-4 rounded-lg border bg-muted/30 space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="font-medium text-sm">Edit Metadata</h4>
        <Button variant="ghost" size="icon" className="h-6 w-6" onClick={onCancel}>
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div className="text-xs text-muted-foreground bg-muted/50 p-2 rounded font-mono truncate" title={torrent.name}>
        {torrent.name}
      </div>

      <div className="grid gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="debrid-edit-title" className="text-xs">
            Title
          </Label>
          <Input
            id="debrid-edit-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Enter title..."
            className="h-8 text-sm"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="debrid-edit-year" className="text-xs">
              Year
            </Label>
            <Input
              id="debrid-edit-year"
              type="number"
              value={year}
              onChange={(e) => setYear(e.target.value)}
              placeholder="YYYY"
              className="h-8 text-sm"
              min={1900}
              max={2100}
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="debrid-edit-type" className="text-xs">
              Type
            </Label>
            <Select value={type} onValueChange={(v) => setType(v as 'movie' | 'series')}>
              <SelectTrigger id="debrid-edit-type" className="h-8 text-sm">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="movie">
                  <div className="flex items-center gap-2">
                    <Film className="h-3.5 w-3.5" />
                    Movie
                  </div>
                </SelectItem>
                <SelectItem value="series">
                  <div className="flex items-center gap-2">
                    <Tv className="h-3.5 w-3.5" />
                    Series
                  </div>
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      <Button
        variant="outline"
        size="sm"
        className="w-full border-dashed border-primary/40 text-primary hover:bg-primary/10"
        onClick={onAdvancedImport}
      >
        <Settings2 className="mr-2 h-4 w-4" />
        Advanced Import with File Annotation
      </Button>

      <div className="flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave}>
          <Save className="mr-1.5 h-3.5 w-3.5" />
          Save
        </Button>
      </div>
    </div>
  )
}

function ImportResultDisplay({ result }: { result: ImportResultItem }) {
  const statusIcon = {
    success: <CheckCircle2 className="h-4 w-4 text-green-500" />,
    failed: <XCircle className="h-4 w-4 text-red-500" />,
    skipped: <AlertCircle className="h-4 w-4 text-yellow-500" />,
  }[result.status]

  return (
    <div className="flex items-start gap-2 p-2 rounded border border-border/50">
      {statusIcon}
      <div className="flex-1 min-w-0">
        <p className="text-xs font-mono truncate" title={result.info_hash}>
          {result.info_hash.slice(0, 12)}...
        </p>
        {result.media_title && (
          <p className="text-xs text-green-600 dark:text-green-400 truncate">&rarr; {result.media_title}</p>
        )}
        {result.message && (
          <p className="text-xs text-muted-foreground truncate" title={result.message}>
            {result.message}
          </p>
        )}
      </div>
    </div>
  )
}

export function DebridTab() {
  // Profile selection
  const { data: profiles } = useProfiles()
  const [selectedProfileId, setSelectedProfileId] = useState<number | undefined>()

  // Provider selection
  const [selectedProvider, setSelectedProvider] = useState<string | undefined>()

  // Import state
  const [selectedHashes, setSelectedHashes] = useState<Set<string>>(new Set())
  const [importResults, setImportResults] = useState<ImportResultItem[] | null>(null)
  const [editingHash, setEditingHash] = useState<string | null>(null)
  const [edits, setEdits] = useState<Map<string, TorrentEdit>>(new Map())
  const [advancedImportTorrent, setAdvancedImportTorrent] = useState<MissingTorrentItem | null>(null)

  // Set default profile on load
  if (profiles && profiles.length > 0 && selectedProfileId === undefined) {
    const defaultProfile = profiles.find((p) => p.is_default) || profiles[0]
    setSelectedProfileId(defaultProfile.id)
  }

  // Fetch providers for the selected profile
  const { data: providersData, isLoading: providersLoading } = useWatchlistProviders(selectedProfileId, {
    enabled: selectedProfileId !== undefined,
  })

  // Set default provider when providers load
  const [prevProviders, setPrevProviders] = useState(providersData?.providers)
  if (prevProviders !== providersData?.providers) {
    setPrevProviders(providersData?.providers)
    if (providersData?.providers && providersData.providers.length > 0) {
      const importProvider = providersData.providers.find((p) => IMPORT_SUPPORTED_PROVIDERS.has(p.service))
      setSelectedProvider(importProvider?.service || providersData.providers[0].service)
    } else {
      setSelectedProvider(undefined)
    }
  }

  const supportsImport = selectedProvider && IMPORT_SUPPORTED_PROVIDERS.has(selectedProvider)

  // Manual fetch trigger -- the user must click "Fetch" to load missing torrents
  const [fetchRequested, setFetchRequested] = useState(false)

  // Fetch missing torrents (only when user explicitly requests)
  const {
    data: missingData,
    isLoading: loadingMissing,
    refetch: refetchMissing,
  } = useMissingTorrents(supportsImport ? selectedProvider : undefined, selectedProfileId, {
    enabled: fetchRequested && !!supportsImport && selectedProfileId !== undefined,
  })

  const importMutation = useImportTorrents()

  const handleFetchMissing = useCallback(() => {
    setFetchRequested(true)
    // If the query was already enabled once, refetch to get fresh data
    if (fetchRequested) {
      refetchMissing()
    }
  }, [fetchRequested, refetchMissing])

  const missingTorrents = useMemo(() => missingData?.items || [], [missingData?.items])
  const matchedTorrents = useMemo(
    () => missingTorrents.filter((torrent) => hasResolvedExternalIds(torrent)),
    [missingTorrents],
  )
  const unmatchedTorrents = useMemo(
    () => missingTorrents.filter((torrent) => !hasResolvedExternalIds(torrent)),
    [missingTorrents],
  )
  const allSelected = missingTorrents.length > 0 && selectedHashes.size === missingTorrents.length
  const someSelected = selectedHashes.size > 0

  const editingTorrent = editingHash ? missingTorrents.find((t) => t.info_hash === editingHash) : null

  const providers = providersData?.providers || []
  const importProviders = providers.filter((p) => IMPORT_SUPPORTED_PROVIDERS.has(p.service))
  const hasProviders = providers.length > 0

  const getTabDisplayName = (provider: WatchlistProviderInfo): string => {
    const serviceName = DEBRID_SERVICE_DISPLAY_NAMES[provider.service] || provider.service
    if (provider.name && provider.name !== serviceName && provider.name !== provider.service) {
      return provider.name
    }
    return serviceName
  }

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedHashes(new Set())
    } else {
      setSelectedHashes(new Set(missingTorrents.map((t) => t.info_hash)))
    }
  }

  const handleSelectMatchedOnly = useCallback(() => {
    setSelectedHashes(new Set(matchedTorrents.map((t) => t.info_hash)))
  }, [matchedTorrents])

  const handleSelectUnmatchedOnly = useCallback(() => {
    setSelectedHashes(new Set(unmatchedTorrents.map((t) => t.info_hash)))
  }, [unmatchedTorrents])

  const handleSelect = (infoHash: string, selected: boolean) => {
    const newSet = new Set(selectedHashes)
    if (selected) {
      newSet.add(infoHash)
    } else {
      newSet.delete(infoHash)
    }
    setSelectedHashes(newSet)
  }

  const handleAdvancedImport = useCallback((torrent: MissingTorrentItem) => {
    setAdvancedImportTorrent(torrent)
  }, [])

  const handleAdvancedImportClose = useCallback(() => {
    setAdvancedImportTorrent(null)
  }, [])

  const handleAdvancedImportSuccess = useCallback(() => {
    if (advancedImportTorrent) {
      setSelectedHashes((prev) => {
        const newSet = new Set(prev)
        newSet.delete(advancedImportTorrent.info_hash)
        return newSet
      })
      setEdits((prev) => {
        const newMap = new Map(prev)
        newMap.delete(advancedImportTorrent.info_hash)
        return newMap
      })
    }
    setAdvancedImportTorrent(null)
  }, [advancedImportTorrent])

  const handleEditSave = useCallback((hash: string, edit: TorrentEdit) => {
    setEdits((prev) => {
      const newMap = new Map(prev)
      newMap.set(hash, edit)
      return newMap
    })
    setEditingHash(null)
  }, [])

  const handleEditCancel = useCallback(() => {
    setEditingHash(null)
  }, [])

  const handleImport = async () => {
    if (selectedHashes.size === 0 || !selectedProvider) return

    setImportResults(null)

    const overrides: Record<string, { title?: string; year?: number; type?: 'movie' | 'series' }> = {}
    edits.forEach((edit, hash) => {
      if (edit.title || edit.year || edit.type) {
        overrides[hash] = edit
      }
    })

    const result = await importMutation.mutateAsync({
      provider: selectedProvider,
      infoHashes: Array.from(selectedHashes),
      profileId: selectedProfileId,
      overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
    })

    setImportResults(result.details)

    const successHashes = new Set(result.details.filter((r) => r.status === 'success').map((r) => r.info_hash))
    setSelectedHashes((prev) => {
      const newSet = new Set(prev)
      successHashes.forEach((h) => newSet.delete(h))
      return newSet
    })
    setEdits((prev) => {
      const newMap = new Map(prev)
      successHashes.forEach((h) => newMap.delete(h))
      return newMap
    })
  }

  // Reset selection when provider changes
  const handleProviderChange = (provider: string) => {
    setSelectedProvider(provider)
    setSelectedHashes(new Set())
    setImportResults(null)
    setEditingHash(null)
    setEdits(new Map())
    setFetchRequested(false)
  }

  const importProgress = importMutation.isPending ? (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-sm">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>Importing {selectedHashes.size} torrent(s)...</span>
      </div>
      <Progress value={undefined} className="h-1" />
    </div>
  ) : null

  if (!selectedProfileId) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Config Bar: Profile + Provider selection */}
      <Card className="glass border-border/50">
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Debrid Account</CardTitle>
          <CardDescription className="text-sm">
            Import torrents from your debrid account that aren&apos;t in the database yet
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-4">
            {/* Profile Selector */}
            {profiles && profiles.length > 1 && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Profile</Label>
                <Select
                  value={selectedProfileId?.toString()}
                  onValueChange={(value) => setSelectedProfileId(parseInt(value, 10))}
                >
                  <SelectTrigger className="w-[180px] rounded-xl">
                    <SelectValue placeholder="Select Profile" />
                  </SelectTrigger>
                  <SelectContent>
                    {profiles.map((profile) => (
                      <SelectItem key={profile.id} value={profile.id.toString()}>
                        <div className="flex items-center gap-2">
                          <span>{profile.name}</span>
                          {profile.is_default && (
                            <Badge variant="secondary" className="text-[10px] px-1 py-0">
                              Default
                            </Badge>
                          )}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {/* Provider Selector */}
            {!providersLoading && hasProviders && (
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Debrid Provider</Label>
                <Select value={selectedProvider} onValueChange={handleProviderChange}>
                  <SelectTrigger className="w-[200px] rounded-xl">
                    <SelectValue placeholder="Select Provider" />
                  </SelectTrigger>
                  <SelectContent>
                    {importProviders.map((provider) => (
                      <SelectItem key={provider.service} value={provider.service}>
                        <div className="flex items-center gap-2">
                          <HardDrive className="h-4 w-4" />
                          {getTabDisplayName(provider)}
                        </div>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* No Providers Configured */}
      {!providersLoading && !hasProviders && (
        <Card className="glass border-border/50">
          <CardContent className="py-12 text-center">
            <CloudOff className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
            <p className="mt-4 font-medium">No Debrid Providers Configured</p>
            <p className="text-sm text-muted-foreground mt-2 max-w-md mx-auto">
              Configure a debrid service in your profile to import content from your debrid account.
            </p>
            <Button className="mt-4 rounded-xl" asChild>
              <Link to="/dashboard/configure">
                <Settings className="mr-2 h-4 w-4" />
                Configure Profile
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* No import-capable providers */}
      {!providersLoading && hasProviders && importProviders.length === 0 && (
        <Card className="glass border-border/50">
          <CardContent className="py-12 text-center">
            <AlertCircle className="h-16 w-16 mx-auto text-muted-foreground opacity-50" />
            <p className="mt-4 font-medium">Import Not Supported</p>
            <p className="text-sm text-muted-foreground mt-2 max-w-md mx-auto">
              Your configured debrid providers do not support the import feature.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Provider selected but doesn't support import */}
      {selectedProvider && !supportsImport && importProviders.length > 0 && (
        <Card className="glass border-border/50">
          <CardContent className="py-8 text-center">
            <AlertCircle className="h-12 w-12 mx-auto text-yellow-500 opacity-50" />
            <p className="mt-4 font-medium">Import not supported for this provider</p>
            <p className="text-sm text-muted-foreground mt-1">Select a different provider from the dropdown above.</p>
          </CardContent>
        </Card>
      )}

      {/* Missing Torrents List */}
      {supportsImport && (
        <Card className="glass border-border/50">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <div>
                <CardTitle className="text-base flex items-center gap-2">
                  <Download className="h-5 w-5" />
                  Missing Torrents
                  {missingTorrents.length > 0 && (
                    <Badge variant="secondary" className="ml-1">
                      {missingTorrents.length}
                    </Badge>
                  )}
                </CardTitle>
                <CardDescription className="text-sm mt-1">
                  Torrents in your {DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider] || selectedProvider} account that
                  aren&apos;t in the database
                </CardDescription>
              </div>
              <Button onClick={handleFetchMissing} disabled={loadingMissing} size="sm" className="rounded-lg">
                {loadingMissing ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Fetching...
                  </>
                ) : (
                  <>
                    <Download className="mr-2 h-4 w-4" />
                    {fetchRequested ? 'Refresh' : 'Fetch'}
                  </>
                )}
              </Button>
            </div>
          </CardHeader>
          <CardContent className="space-y-4">
            {!fetchRequested ? (
              <div className="text-center py-12">
                <Download className="h-12 w-12 mx-auto text-muted-foreground opacity-30" />
                <p className="mt-4 font-medium">Ready to Scan</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Click the Fetch button to scan your{' '}
                  {DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider] || selectedProvider} account for missing torrents.
                </p>
              </div>
            ) : loadingMissing ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
              </div>
            ) : missingTorrents.length === 0 ? (
              <div className="text-center py-12">
                <CheckCircle2 className="h-12 w-12 mx-auto text-green-500 opacity-50" />
                <p className="mt-4 font-medium">All Synced!</p>
                <p className="text-sm text-muted-foreground mt-1">
                  All your {DEBRID_SERVICE_DISPLAY_NAMES[selectedProvider] || selectedProvider} torrents are already in
                  the database.
                </p>
              </div>
            ) : (
              <>
                {/* Selection header */}
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <div className="flex flex-wrap items-center gap-2">
                    <Checkbox
                      checked={allSelected}
                      onCheckedChange={handleSelectAll}
                      disabled={importMutation.isPending}
                    />
                    <span className="text-sm">
                      {someSelected
                        ? `${selectedHashes.size} of ${missingTorrents.length} selected`
                        : `${missingTorrents.length} missing torrent(s)`}
                    </span>
                    <Badge variant="outline" className="text-[10px]">
                      Matched: {matchedTorrents.length}
                    </Badge>
                    <Badge variant="outline" className="text-[10px]">
                      Not matched: {unmatchedTorrents.length}
                    </Badge>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSelectMatchedOnly}
                      disabled={importMutation.isPending || matchedTorrents.length === 0}
                    >
                      Select matched
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleSelectUnmatchedOnly}
                      disabled={importMutation.isPending || unmatchedTorrents.length === 0}
                    >
                      Select not matched
                    </Button>
                  </div>
                  <div className="flex items-center gap-2">
                    {someSelected && !importMutation.isPending && (
                      <Button variant="ghost" size="sm" onClick={() => setSelectedHashes(new Set())}>
                        Clear selection
                      </Button>
                    )}
                    <Button onClick={handleImport} disabled={!someSelected || importMutation.isPending} size="sm">
                      {importMutation.isPending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Importing...
                        </>
                      ) : (
                        <>
                          <Download className="mr-2 h-4 w-4" />
                          Import {selectedHashes.size > 0 ? `(${selectedHashes.size})` : 'Selected'}
                        </>
                      )}
                    </Button>
                  </div>
                </div>

                {/* Progress or Results */}
                {importProgress}

                {importResults && (
                  <div className="space-y-2 p-3 rounded-lg bg-muted/50">
                    <div className="flex items-center gap-4 text-sm">
                      <span className="text-green-600 dark:text-green-400">
                        {importResults.filter((r) => r.status === 'success').length} imported
                      </span>
                      <span className="text-red-600 dark:text-red-400">
                        {importResults.filter((r) => r.status === 'failed').length} failed
                      </span>
                      <span className="text-yellow-600 dark:text-yellow-400">
                        {importResults.filter((r) => r.status === 'skipped').length} skipped
                      </span>
                    </div>
                    <ScrollArea className="h-32">
                      <div className="space-y-1.5">
                        {importResults.map((result) => (
                          <ImportResultDisplay key={result.info_hash} result={result} />
                        ))}
                      </div>
                    </ScrollArea>
                  </div>
                )}

                {/* Edit Panel */}
                {editingTorrent && (
                  <EditPanel
                    torrent={editingTorrent}
                    edit={edits.get(editingHash!)}
                    onSave={(edit) => handleEditSave(editingHash!, edit)}
                    onCancel={handleEditCancel}
                    onAdvancedImport={() => handleAdvancedImport(editingTorrent)}
                  />
                )}

                {/* Torrent list */}
                <ScrollArea className="h-[400px] pr-4">
                  <div className="space-y-2">
                    {matchedTorrents.length > 0 && (
                      <div className="flex items-center gap-2 pb-1 pt-0.5">
                        <Badge variant="secondary" className="h-5 px-2 text-[10px]">
                          Matched
                        </Badge>
                        <span className="text-xs text-muted-foreground">{matchedTorrents.length} item(s)</span>
                      </div>
                    )}
                    {matchedTorrents.map((torrent) => (
                      <TorrentItem
                        key={torrent.info_hash}
                        torrent={torrent}
                        selected={selectedHashes.has(torrent.info_hash)}
                        onSelect={(selected) => handleSelect(torrent.info_hash, selected)}
                        disabled={importMutation.isPending}
                        isEditing={editingHash === torrent.info_hash}
                        onEditClick={() => setEditingHash(editingHash === torrent.info_hash ? null : torrent.info_hash)}
                        edit={edits.get(torrent.info_hash)}
                      />
                    ))}

                    {unmatchedTorrents.length > 0 && (
                      <div className="flex items-center gap-2 pb-1 pt-3">
                        <Badge variant="destructive" className="h-5 px-2 text-[10px]">
                          Not matched
                        </Badge>
                        <span className="text-xs text-muted-foreground">{unmatchedTorrents.length} item(s)</span>
                      </div>
                    )}
                    {unmatchedTorrents.map((torrent) => (
                      <TorrentItem
                        key={torrent.info_hash}
                        torrent={torrent}
                        selected={selectedHashes.has(torrent.info_hash)}
                        onSelect={(selected) => handleSelect(torrent.info_hash, selected)}
                        disabled={importMutation.isPending}
                        isEditing={editingHash === torrent.info_hash}
                        onEditClick={() => setEditingHash(editingHash === torrent.info_hash ? null : torrent.info_hash)}
                        edit={edits.get(torrent.info_hash)}
                      />
                    ))}
                  </div>
                </ScrollArea>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {/* Advanced Import Dialog */}
      {advancedImportTorrent && selectedProvider && (
        <AdvancedImportDialog
          open={!!advancedImportTorrent}
          onOpenChange={(open) => !open && handleAdvancedImportClose()}
          torrent={advancedImportTorrent}
          provider={selectedProvider}
          profileId={selectedProfileId}
          onSuccess={handleAdvancedImportSuccess}
        />
      )}
    </div>
  )
}
