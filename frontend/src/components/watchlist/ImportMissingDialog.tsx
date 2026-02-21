import { useState, useMemo, useCallback } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Badge } from '@/components/ui/badge'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Progress } from '@/components/ui/progress'
import { Switch } from '@/components/ui/switch'
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
} from 'lucide-react'
import { useAuth, useMissingTorrents, useImportTorrents } from '@/hooks'
import type { MissingTorrentItem, ImportResultItem } from '@/lib/api/watchlist'
import {
  getStoredAnonymousDisplayName,
  normalizeAnonymousDisplayName,
  saveAnonymousDisplayName,
} from '@/lib/anonymousDisplayName'
import { cn } from '@/lib/utils'
import { AdvancedImportDialog } from './AdvancedImportDialog'

interface ImportMissingDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  provider: string
  providerName?: string
  profileId?: number
}

// Store user edits for torrents
interface TorrentEdit {
  title?: string
  year?: number
  type?: 'movie' | 'series'
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

  // Use edited values if available
  const displayTitle = edit?.title || torrent.parsed_title
  const displayYear = edit?.year || torrent.parsed_year
  const displayType = edit?.type || torrent.parsed_type
  const hasEdits = edit && (edit.title || edit.year || edit.type)

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

      {/* Edit button - on left side for visibility */}
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
          <Label htmlFor="edit-title" className="text-xs">
            Title
          </Label>
          <Input
            id="edit-title"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Enter title..."
            className="h-8 text-sm"
          />
        </div>

        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="edit-year" className="text-xs">
              Year
            </Label>
            <Input
              id="edit-year"
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
            <Label htmlFor="edit-type" className="text-xs">
              Type
            </Label>
            <Select value={type} onValueChange={(v) => setType(v as 'movie' | 'series')}>
              <SelectTrigger id="edit-type" className="h-8 text-sm">
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
          <p className="text-xs text-green-600 dark:text-green-400 truncate">→ {result.media_title}</p>
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

export function ImportMissingDialog({
  open,
  onOpenChange,
  provider,
  providerName,
  profileId,
}: ImportMissingDialogProps) {
  const { user } = useAuth()
  const [selectedHashes, setSelectedHashes] = useState<Set<string>>(new Set())
  const [importResults, setImportResults] = useState<ImportResultItem[] | null>(null)
  const [editingHash, setEditingHash] = useState<string | null>(null)
  const [edits, setEdits] = useState<Map<string, TorrentEdit>>(new Map())
  const [advancedImportTorrent, setAdvancedImportTorrent] = useState<MissingTorrentItem | null>(null)
  const [isAnonymous, setIsAnonymous] = useState(user?.contribute_anonymously ?? false)
  const [anonymousDisplayName, setAnonymousDisplayName] = useState(getStoredAnonymousDisplayName())

  const { data: missingData, isLoading: loadingMissing } = useMissingTorrents(provider, profileId, { enabled: open })

  const importMutation = useImportTorrents()

  const missingTorrents = missingData?.items || []
  const allSelected = missingTorrents.length > 0 && selectedHashes.size === missingTorrents.length
  const someSelected = selectedHashes.size > 0

  const editingTorrent = editingHash ? missingTorrents.find((t) => t.info_hash === editingHash) : null

  const handleSelectAll = () => {
    if (allSelected) {
      setSelectedHashes(new Set())
    } else {
      setSelectedHashes(new Set(missingTorrents.map((t) => t.info_hash)))
    }
  }

  const handleSelect = (infoHash: string, selected: boolean) => {
    const newSet = new Set(selectedHashes)
    if (selected) {
      newSet.add(infoHash)
    } else {
      newSet.delete(infoHash)
    }
    setSelectedHashes(newSet)
  }

  // Open the advanced import dialog for a specific torrent
  const handleAdvancedImport = useCallback((torrent: MissingTorrentItem) => {
    setAdvancedImportTorrent(torrent)
  }, [])

  const handleAdvancedImportClose = useCallback(() => {
    setAdvancedImportTorrent(null)
  }, [])

  const handleAdvancedImportSuccess = useCallback(() => {
    // Close the advanced dialog and remove the torrent from selection
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
    if (selectedHashes.size === 0) return

    setImportResults(null)
    const normalizedAnonymousDisplayName = isAnonymous ? normalizeAnonymousDisplayName(anonymousDisplayName) : undefined

    // Build overrides object from edits
    const overrides: Record<string, { title?: string; year?: number; type?: 'movie' | 'series' }> = {}
    edits.forEach((edit, hash) => {
      if (edit.title || edit.year || edit.type) {
        overrides[hash] = edit
      }
    })

    const result = await importMutation.mutateAsync({
      provider,
      infoHashes: Array.from(selectedHashes),
      profileId,
      overrides: Object.keys(overrides).length > 0 ? overrides : undefined,
      isAnonymous,
      anonymousDisplayName: normalizedAnonymousDisplayName,
    })

    setImportResults(result.details)

    // Remove successfully imported hashes from selection and edits
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

  const handleClose = () => {
    setSelectedHashes(new Set())
    setImportResults(null)
    setEditingHash(null)
    setEdits(new Map())
    onOpenChange(false)
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

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="max-w-2xl max-h-[85vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Download className="h-5 w-5" />
            Import Missing Torrents
          </DialogTitle>
          <DialogDescription>
            Import torrents from your {providerName || provider} account that aren't in our database yet. Click the edit
            icon to modify detected metadata before importing.
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 space-y-4">
          {loadingMissing ? (
            <div className="flex items-center justify-center py-12">
              <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            </div>
          ) : missingTorrents.length === 0 ? (
            <div className="text-center py-12">
              <CheckCircle2 className="h-12 w-12 mx-auto text-green-500 opacity-50" />
              <p className="mt-4 font-medium">All Synced!</p>
              <p className="text-sm text-muted-foreground mt-1">
                All your {providerName || provider} torrents are already in our database.
              </p>
            </div>
          ) : (
            <>
              {/* Header with select all */}
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
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
                </div>
                {someSelected && !importMutation.isPending && (
                  <Button variant="ghost" size="sm" onClick={() => setSelectedHashes(new Set())}>
                    Clear selection
                  </Button>
                )}
              </div>

              <div className="space-y-2 rounded-md border border-border/50 p-3">
                <div className="flex items-center justify-between">
                  <Label className="text-sm text-muted-foreground">Anonymous contribution</Label>
                  <Switch checked={isAnonymous} onCheckedChange={setIsAnonymous} />
                </div>
                {isAnonymous && (
                  <div className="space-y-1">
                    <Input
                      placeholder="Anonymous display name (optional)"
                      value={anonymousDisplayName}
                      onChange={(e) => {
                        setAnonymousDisplayName(e.target.value)
                        saveAnonymousDisplayName(e.target.value)
                      }}
                    />
                    <p className="text-xs text-muted-foreground">
                      Stream uploader uses this name. Leave empty to use &quot;Anonymous&quot;.
                    </p>
                  </div>
                )}
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
              <ScrollArea className="h-[300px] pr-4">
                <div className="space-y-2">
                  {missingTorrents.map((torrent) => (
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
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={handleClose}>
            {importResults ? 'Done' : 'Cancel'}
          </Button>
          {missingTorrents.length > 0 && (
            <Button onClick={handleImport} disabled={!someSelected || importMutation.isPending}>
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
          )}
        </DialogFooter>
      </DialogContent>

      {/* Advanced Import Dialog */}
      {advancedImportTorrent && (
        <AdvancedImportDialog
          open={!!advancedImportTorrent}
          onOpenChange={(open) => !open && handleAdvancedImportClose()}
          torrent={advancedImportTorrent}
          provider={provider}
          profileId={profileId}
          initialIsAnonymous={isAnonymous}
          initialAnonymousDisplayName={anonymousDisplayName}
          onSuccess={handleAdvancedImportSuccess}
        />
      )}
    </Dialog>
  )
}
