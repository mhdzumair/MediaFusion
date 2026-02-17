import { useState, useCallback, useEffect } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import {
  Info,
  Play,
  Loader2,
  CheckCircle2,
  AlertCircle,
  FileVideo,
  ArrowDown,
  FolderTree,
  FileText,
  Eraser,
  CheckSquare,
  Square,
  HelpCircle,
} from 'lucide-react'
import { cn } from '@/lib/utils'

export interface FileLink {
  file_id: number
  file_name: string
  size?: number | null // File size in bytes
  season_number: number | null
  episode_number: number | null
  episode_end?: number | null
}

export interface EditedFileLink extends FileLink {
  included: boolean
  isModified?: boolean
}

type ViewMode = 'full' | 'filename'

interface FileAnnotationDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  streamName: string
  initialFiles: FileLink[]
  onSave: (files: EditedFileLink[]) => Promise<void>
  isLoading?: boolean
}

// Extract just the filename from a full path
function getFilenameOnly(fullPath: string): string {
  const parts = fullPath.split('/')
  return parts[parts.length - 1] || fullPath
}

// Get folder structure (everything except the filename)
function getFolderPath(fullPath: string): string {
  const parts = fullPath.split('/')
  if (parts.length <= 1) return ''
  return parts.slice(0, -1).join('/') + '/'
}

// Format file size
function formatFileSize(bytes: number | null | undefined): string {
  if (bytes == null || bytes === 0) return ''
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let unitIndex = 0
  let size = bytes
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`
}

export function FileAnnotationDialog({
  open,
  onOpenChange,
  streamName,
  initialFiles,
  onSave,
  isLoading = false,
}: FileAnnotationDialogProps) {
  const [editedFiles, setEditedFiles] = useState<EditedFileLink[]>([])
  const [highlightedIndices, setHighlightedIndices] = useState<Set<number>>(new Set())
  const [saveError, setSaveError] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<ViewMode>('filename')

  // Initialize edited files from initial files
  useEffect(() => {
    if (open && initialFiles.length > 0) {
      const sorted = [...initialFiles].sort((a, b) =>
        a.file_name.localeCompare(b.file_name, undefined, {
          numeric: true,
          sensitivity: 'base',
        }),
      )
      setEditedFiles(
        sorted.map((f) => ({
          ...f,
          included: true,
          isModified: false,
        })),
      )
      setSaveError(null)
    }
  }, [open, initialFiles])

  const updateFile = useCallback(
    (fileId: number, field: keyof EditedFileLink, value: number | null | boolean) => {
      setEditedFiles((prev) =>
        prev.map((f) => {
          if (f.file_id !== fileId) return f
          const original = initialFiles.find((o) => o.file_id === fileId)
          const updated = { ...f, [field]: value }

          // Check if modified from original
          if (original) {
            updated.isModified =
              updated.season_number !== original.season_number ||
              updated.episode_number !== original.episode_number ||
              updated.episode_end !== original.episode_end
          }
          return updated
        }),
      )
    },
    [initialFiles],
  )

  const toggleFileInclusion = useCallback((fileId: number) => {
    setEditedFiles((prev) => prev.map((f) => (f.file_id === fileId ? { ...f, included: !f.included } : f)))
  }, [])

  // Select/deselect all files
  const toggleAllFiles = useCallback((include: boolean) => {
    setEditedFiles((prev) => prev.map((f) => ({ ...f, included: include })))
  }, [])

  // Clear all episode data
  const clearAllEpisodeData = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => {
        const original = initialFiles.find((o) => o.file_id === f.file_id)
        return {
          ...f,
          season_number: null,
          episode_number: null,
          episode_end: null,
          isModified:
            original?.season_number !== null || original?.episode_number !== null || original?.episode_end !== null,
        }
      }),
    )
  }, [initialFiles])

  // Apply same season to all following files
  const applySeasonToFollowing = useCallback(
    (startIndex: number) => {
      const startFile = editedFiles[startIndex]
      if (!startFile || startFile.season_number === null) return

      const seasonNum = startFile.season_number
      const indicesToHighlight: number[] = []

      setEditedFiles((prev) =>
        prev.map((f, idx) => {
          if (idx < startIndex || !f.included) return f
          indicesToHighlight.push(idx)
          const original = initialFiles.find((o) => o.file_id === f.file_id)
          return {
            ...f,
            season_number: seasonNum,
            isModified:
              seasonNum !== original?.season_number ||
              f.episode_number !== original?.episode_number ||
              f.episode_end !== original?.episode_end,
          }
        }),
      )

      // Visual feedback
      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles, initialFiles],
  )

  // Apply consecutive episode numbering from index
  // If the starting file has an episode number, continue from there
  // Otherwise start from 1
  const applyEpisodeNumbering = useCallback(
    (startIndex: number) => {
      const startFile = editedFiles[startIndex]
      // Start from current episode number if set, otherwise 1
      let episodeCounter = startFile?.episode_number ?? 1
      let lastSeason: number | null = null
      const indicesToHighlight: number[] = []

      setEditedFiles((prev) =>
        prev.map((f, idx) => {
          if (idx < startIndex || !f.included) return f

          // Reset episode counter if season changes (and we're not at the start)
          const currentSeason = f.season_number
          if (idx !== startIndex && lastSeason !== null && currentSeason !== null && currentSeason !== lastSeason) {
            episodeCounter = 1
          }

          indicesToHighlight.push(idx)
          const newEpisodeNumber = episodeCounter++
          if (currentSeason !== null) {
            lastSeason = currentSeason
          }

          const original = initialFiles.find((o) => o.file_id === f.file_id)
          return {
            ...f,
            episode_number: newEpisodeNumber,
            isModified:
              f.season_number !== original?.season_number ||
              newEpisodeNumber !== original?.episode_number ||
              f.episode_end !== original?.episode_end,
          }
        }),
      )

      // Visual feedback
      setHighlightedIndices(new Set(indicesToHighlight))
      setTimeout(() => setHighlightedIndices(new Set()), 1500)
    },
    [editedFiles, initialFiles],
  )

  const handleSave = async () => {
    try {
      setSaveError(null)
      // Only save modified and included files
      const modifiedFiles = editedFiles.filter((f) => f.included && f.isModified)
      await onSave(modifiedFiles)
      onOpenChange(false)
    } catch (error) {
      setSaveError((error as Error)?.message || 'Failed to save changes')
    }
  }

  const modifiedCount = editedFiles.filter((f) => f.included && f.isModified).length
  const includedCount = editedFiles.filter((f) => f.included).length

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px] h-[85vh] flex flex-col p-0 gap-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-4 border-b flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <FileVideo className="h-5 w-5 text-emerald-500" />
            Annotate Video Files
          </DialogTitle>
          <DialogDescription className="text-sm">
            Fix season and episode numbers for files in{' '}
            <span className="font-medium text-foreground break-all">{streamName}</span>
          </DialogDescription>
        </DialogHeader>

        {/* Toolbar */}
        <div className="px-6 py-3 border-b bg-muted/30 flex-shrink-0">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              {/* View Mode Toggle */}
              <div className="bg-background/50 p-0.5 rounded-lg flex gap-0.5">
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn('h-7 px-2', viewMode === 'filename' && 'bg-emerald-500/20')}
                        onClick={() => setViewMode('filename')}
                      >
                        <FileText className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Filename only</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
                <TooltipProvider>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="sm"
                        className={cn('h-7 px-2', viewMode === 'full' && 'bg-emerald-500/20')}
                        onClick={() => setViewMode('full')}
                      >
                        <FolderTree className="h-3.5 w-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Full path with folder structure</TooltipContent>
                  </Tooltip>
                </TooltipProvider>
              </div>

              <Separator orientation="vertical" className="h-6" />

              {/* Selection buttons */}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => toggleAllFiles(true)}>
                      <CheckSquare className="h-3.5 w-3.5 mr-1" />
                      All
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Select all files</TooltipContent>
                </Tooltip>
              </TooltipProvider>

              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs"
                      onClick={() => toggleAllFiles(false)}
                    >
                      <Square className="h-3.5 w-3.5 mr-1" />
                      None
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Deselect all files</TooltipContent>
                </Tooltip>
              </TooltipProvider>

              <Separator orientation="vertical" className="h-6" />

              {/* Clear all button */}
              <TooltipProvider>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-7 px-2 text-xs text-primary hover:text-primary"
                      onClick={clearAllEpisodeData}
                    >
                      <Eraser className="h-3.5 w-3.5 mr-1" />
                      Clear All
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Clear all season/episode data</TooltipContent>
                </Tooltip>
              </TooltipProvider>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="outline" className="font-normal">
                {editedFiles.length} files
              </Badge>
              <Badge variant="outline" className="font-normal">
                {includedCount} selected
              </Badge>
              {modifiedCount > 0 && (
                <Badge className="bg-emerald-500/20 text-emerald-500 border-emerald-500/30">
                  {modifiedCount} modified
                </Badge>
              )}
            </div>
          </div>
        </div>

        {/* Info Alert */}
        <div className="px-6 py-2 bg-blue-500/5 border-b flex-shrink-0">
          <div className="flex items-start gap-2 text-xs text-blue-400">
            <Info className="h-3.5 w-3.5 mt-0.5 flex-shrink-0" />
            <span>
              Use <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">↓</kbd> to apply season to following
              files, and <kbd className="px-1 py-0.5 rounded bg-blue-500/20 text-[10px]">▶</kbd> for consecutive episode
              numbering (continues from current or starts at 1).
            </span>
          </div>
        </div>

        {/* File List */}
        <ScrollArea className="flex-1 min-h-0">
          <div className="px-6 py-4 space-y-2">
            {editedFiles.map((file, index) => {
              const filename = getFilenameOnly(file.file_name)
              const folderPath = getFolderPath(file.file_name)

              return (
                <div
                  key={file.file_id}
                  className={cn(
                    'p-3 rounded-lg border transition-all duration-300',
                    !file.included && 'opacity-40 bg-muted/20',
                    file.isModified && file.included && 'border-emerald-500/50 bg-emerald-500/5',
                    highlightedIndices.has(index) && 'ring-2 ring-emerald-500/50',
                    file.included && !file.isModified && 'border-border/40 bg-background/50 hover:border-border',
                  )}
                >
                  {/* Header Row */}
                  <div className="flex items-center gap-2 mb-2">
                    <Switch
                      checked={file.included}
                      onCheckedChange={() => toggleFileInclusion(file.file_id)}
                      className="data-[state=checked]:bg-emerald-500 scale-90 flex-shrink-0"
                    />

                    <div className="flex-1 min-w-0">
                      {viewMode === 'full' && folderPath && (
                        <p className="text-[10px] text-muted-foreground/60 font-mono break-all">{folderPath}</p>
                      )}
                      <p className={cn('font-mono break-all', viewMode === 'filename' ? 'text-sm' : 'text-xs')}>
                        {filename}
                      </p>
                    </div>

                    {file.size != null && file.size > 0 && (
                      <Badge
                        variant="outline"
                        className="text-[10px] font-normal text-muted-foreground flex-shrink-0 whitespace-nowrap"
                      >
                        {formatFileSize(file.size)}
                      </Badge>
                    )}

                    {file.isModified && file.included && (
                      <Badge
                        variant="secondary"
                        className="bg-emerald-500/20 text-emerald-400 text-[10px] flex-shrink-0"
                      >
                        Modified
                      </Badge>
                    )}
                  </div>

                  {/* Input Row */}
                  <div className="grid grid-cols-3 gap-2">
                    {/* Season */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <Label className="text-[10px] text-muted-foreground">Season</Label>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-5 w-5"
                                onClick={() => applySeasonToFollowing(index)}
                                disabled={!file.included || file.season_number === null}
                              >
                                <ArrowDown className="h-3 w-3" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Apply this season to all following files</TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                      <Input
                        type="number"
                        min={0}
                        value={file.season_number ?? ''}
                        onChange={(e) =>
                          updateFile(file.file_id, 'season_number', e.target.value ? parseInt(e.target.value) : null)
                        }
                        disabled={!file.included}
                        className="h-8 text-sm"
                        placeholder="S"
                      />
                    </div>

                    {/* Episode */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <Label className="text-[10px] text-muted-foreground">Episode</Label>
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-5 w-5"
                                onClick={() => applyEpisodeNumbering(index)}
                                disabled={!file.included}
                              >
                                <Play className="h-3 w-3" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent className="max-w-xs">
                              <p>Apply consecutive numbering from here.</p>
                              <p className="text-muted-foreground mt-1">
                                Continues from current episode number, or starts at 1 if empty. Resets when season
                                changes.
                              </p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      </div>
                      <Input
                        type="number"
                        min={0}
                        value={file.episode_number ?? ''}
                        onChange={(e) =>
                          updateFile(file.file_id, 'episode_number', e.target.value ? parseInt(e.target.value) : null)
                        }
                        disabled={!file.included}
                        className="h-8 text-sm"
                        placeholder="E"
                      />
                    </div>

                    {/* Episode End */}
                    <div className="space-y-1">
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-1">
                          <Label className="text-[10px] text-muted-foreground">Ep. End</Label>
                          <TooltipProvider>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <HelpCircle className="h-3 w-3 text-muted-foreground/50 cursor-help" />
                              </TooltipTrigger>
                              <TooltipContent className="max-w-xs">
                                <p className="font-medium">Multi-episode files</p>
                                <p className="text-muted-foreground mt-1">
                                  If a single file contains multiple consecutive episodes (e.g., E01-E03), set Episode
                                  to 1 and Episode End to 3.
                                </p>
                              </TooltipContent>
                            </Tooltip>
                          </TooltipProvider>
                        </div>
                      </div>
                      <Input
                        type="number"
                        min={0}
                        value={file.episode_end ?? ''}
                        onChange={(e) =>
                          updateFile(file.file_id, 'episode_end', e.target.value ? parseInt(e.target.value) : null)
                        }
                        disabled={!file.included}
                        className="h-8 text-sm"
                        placeholder="Multi"
                      />
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </ScrollArea>

        {/* Error */}
        {saveError && (
          <div className="px-6 py-2 bg-red-500/10 border-t flex-shrink-0">
            <div className="flex items-center gap-2 text-xs text-red-400">
              <AlertCircle className="h-3.5 w-3.5" />
              {saveError}
            </div>
          </div>
        )}

        {/* Footer */}
        <DialogFooter className="px-6 py-4 border-t bg-muted/30 flex-shrink-0">
          <div className="flex items-center justify-between w-full">
            <div className="text-sm text-muted-foreground">
              {modifiedCount > 0 ? (
                <span className="text-emerald-500 font-medium">
                  {modifiedCount} file{modifiedCount !== 1 ? 's' : ''} modified
                </span>
              ) : (
                'No changes'
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => onOpenChange(false)} className="rounded-lg">
                Cancel
              </Button>
              <Button
                onClick={handleSave}
                disabled={modifiedCount === 0 || isLoading}
                className="rounded-lg bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 hover:to-teal-500"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                    Saving...
                  </>
                ) : (
                  <>
                    <CheckCircle2 className="h-4 w-4 mr-2" />
                    Save {modifiedCount} Change{modifiedCount !== 1 ? 's' : ''}
                  </>
                )}
              </Button>
            </div>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
