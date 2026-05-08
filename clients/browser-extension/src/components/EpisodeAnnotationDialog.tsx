import { useState, useCallback, useEffect } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Card } from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { 
  FileVideo, 
  ArrowDown, 
  Play,
  CheckSquare,
  Square,
  Eraser,
  X,
  Check,
  Info,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TorrentFile, FileAnnotation } from '@/lib/types'

interface EpisodeAnnotationDialogProps {
  files: TorrentFile[]
  torrentName: string
  onConfirm: (annotations: FileAnnotation[]) => void
  onCancel: () => void
}

// Format file size
function formatFileSize(bytes: number): string {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let unitIndex = 0
  let size = bytes
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex++
  }
  return `${size.toFixed(unitIndex > 0 ? 1 : 0)} ${units[unitIndex]}`
}

// Extract filename from path
function getFilename(fullPath: string): string {
  const parts = fullPath.split('/')
  return parts[parts.length - 1] || fullPath
}

interface EditedFile {
  index: number
  filename: string
  size: number
  season: number | null
  episode: number | null
  episodeEnd: number | null
  included: boolean
  isModified: boolean
}

export function EpisodeAnnotationDialog({
  files,
  torrentName,
  onConfirm,
  onCancel,
}: EpisodeAnnotationDialogProps) {
  const [editedFiles, setEditedFiles] = useState<EditedFile[]>([])
  const [highlightedIndices, setHighlightedIndices] = useState<Set<number>>(new Set())

  // Initialize edited files from props
  useEffect(() => {
    const sorted = [...files]
      .filter(f => {
        // Filter out non-video files
        const ext = f.filename.toLowerCase().split('.').pop() || ''
        return ['mkv', 'mp4', 'avi', 'mov', 'wmv', 'flv', 'webm', 'm4v'].includes(ext)
      })
      .sort((a, b) => 
        a.filename.localeCompare(b.filename, undefined, { numeric: true, sensitivity: 'base' })
      )
    
    setEditedFiles(
      sorted.map((f) => ({
        index: f.index,
        filename: f.filename,
        size: f.size,
        season: f.season_number ?? null,
        episode: f.episode_number ?? null,
        episodeEnd: null,
        included: true,
        isModified: false,
      }))
    )
  }, [files])

  const updateFile = useCallback((
    index: number,
    field: keyof EditedFile,
    value: number | null | boolean
  ) => {
    setEditedFiles((prev) =>
      prev.map((f) => {
        if (f.index !== index) return f
        const original = files.find((o) => o.index === index)
        const updated = { ...f, [field]: value }
        
        // Check if modified from original
        if (original) {
          updated.isModified =
            updated.season !== (original.season_number ?? null) ||
            updated.episode !== (original.episode_number ?? null) ||
            updated.episodeEnd !== null
        } else {
          updated.isModified = true
        }
        return updated
      })
    )
  }, [files])

  const toggleFileInclusion = useCallback((index: number) => {
    setEditedFiles((prev) =>
      prev.map((f) =>
        f.index === index ? { ...f, included: !f.included } : f
      )
    )
  }, [])

  const toggleAllFiles = useCallback((include: boolean) => {
    setEditedFiles((prev) =>
      prev.map((f) => ({ ...f, included: include }))
    )
  }, [])

  const clearAllEpisodeData = useCallback(() => {
    setEditedFiles((prev) =>
      prev.map((f) => ({
        ...f,
        season: null,
        episode: null,
        episodeEnd: null,
        isModified: true,
      }))
    )
  }, [])

  // Apply same season to all following files
  const applySeasonToFollowing = useCallback((startIdx: number) => {
    const editedIndex = editedFiles.findIndex(f => f.index === startIdx)
    if (editedIndex === -1) return
    
    const startFile = editedFiles[editedIndex]
    if (!startFile || startFile.season === null) return

    const seasonNum = startFile.season
    const indicesToHighlight: number[] = []

    setEditedFiles((prev) =>
      prev.map((f, idx) => {
        if (idx < editedIndex || !f.included) return f
        indicesToHighlight.push(idx)
        return {
          ...f,
          season: seasonNum,
          isModified: true,
        }
      })
    )

    setHighlightedIndices(new Set(indicesToHighlight))
    setTimeout(() => setHighlightedIndices(new Set()), 1500)
  }, [editedFiles])

  // Apply consecutive episode numbering
  const applyEpisodeNumbering = useCallback((startIdx: number) => {
    const editedIndex = editedFiles.findIndex(f => f.index === startIdx)
    if (editedIndex === -1) return
    
    const startFile = editedFiles[editedIndex]
    let episodeCounter = startFile?.episode ?? 1
    let lastSeason: number | null = null
    const indicesToHighlight: number[] = []

    setEditedFiles((prev) =>
      prev.map((f, idx) => {
        if (idx < editedIndex || !f.included) return f

        // Reset episode counter if season changes
        const currentSeason = f.season
        if (
          idx !== editedIndex &&
          lastSeason !== null &&
          currentSeason !== null &&
          currentSeason !== lastSeason
        ) {
          episodeCounter = 1
        }

        indicesToHighlight.push(idx)
        const newEpisodeNumber = episodeCounter++
        if (currentSeason !== null) {
          lastSeason = currentSeason
        }

        return {
          ...f,
          episode: newEpisodeNumber,
          isModified: true,
        }
      })
    )

    setHighlightedIndices(new Set(indicesToHighlight))
    setTimeout(() => setHighlightedIndices(new Set()), 1500)
  }, [editedFiles])

  const handleConfirm = () => {
    const annotations: FileAnnotation[] = editedFiles
      .filter(f => f.included)
      .map(f => ({
        index: f.index,
        filename: f.filename,
        size: f.size,
        season: f.season ?? undefined,
        episode: f.episode ?? undefined,
        skip: false,
      }))
    
    onConfirm(annotations)
  }

  const modifiedCount = editedFiles.filter(f => f.included && f.isModified).length
  const includedCount = editedFiles.filter(f => f.included).length

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 border-b flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <FileVideo className="h-5 w-5 text-primary" />
            <div>
              <h3 className="font-semibold text-sm">Episode Annotation</h3>
              <p className="text-xs text-muted-foreground truncate max-w-[200px]" title={torrentName}>
                {torrentName}
              </p>
            </div>
          </div>
          <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={onCancel}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>

      {/* Toolbar */}
      <div className="px-4 py-2 border-b bg-muted/30 flex-shrink-0">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-1">
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => toggleAllFiles(true)}>
                    <CheckSquare className="h-3 w-3 mr-1" />
                    All
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Select all files</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => toggleAllFiles(false)}>
                    <Square className="h-3 w-3 mr-1" />
                    None
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Deselect all files</TooltipContent>
              </Tooltip>
            </TooltipProvider>
            
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button variant="ghost" size="sm" className="h-7 px-2 text-xs text-destructive" onClick={clearAllEpisodeData}>
                    <Eraser className="h-3 w-3 mr-1" />
                    Clear
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Clear all season/episode data</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>

          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="outline" className="font-normal text-[10px]">
              {editedFiles.length} files
            </Badge>
            <Badge variant="outline" className="font-normal text-[10px]">
              {includedCount} selected
            </Badge>
          </div>
        </div>
      </div>

      {/* Info */}
      <div className="px-4 py-2 bg-blue-500/5 border-b flex-shrink-0">
        <div className="flex items-start gap-2 text-[10px] text-blue-400">
          <Info className="h-3 w-3 mt-0.5 flex-shrink-0" />
          <span>
            Use <ArrowDown className="h-2.5 w-2.5 inline" /> to apply season to following, 
            <Play className="h-2.5 w-2.5 inline mx-1" /> for consecutive episode numbering.
          </span>
        </div>
      </div>

      {/* File List */}
      <ScrollArea className="flex-1 min-h-0">
        <div className="p-4 space-y-2">
          {editedFiles.map((file, idx) => (
            <Card
              key={file.index}
              className={cn(
                'p-2 transition-all duration-300',
                !file.included && 'opacity-40',
                file.isModified && file.included && 'border-primary/50 bg-primary/5',
                highlightedIndices.has(idx) && 'ring-2 ring-primary/50'
              )}
            >
              <div className="flex items-center gap-2 mb-2">
                <Switch
                  checked={file.included}
                  onCheckedChange={() => toggleFileInclusion(file.index)}
                  className="scale-75"
                />
                <Badge variant="outline" className="text-[9px] font-normal">
                  {formatFileSize(file.size)}
                </Badge>
                <p className="text-xs font-mono truncate flex-1" title={file.filename}>
                  {getFilename(file.filename)}
                </p>
              </div>

              <div className="grid grid-cols-3 gap-2">
                {/* Season */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <Label className="text-[9px] text-muted-foreground">Season</Label>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-4 w-4"
                            onClick={() => applySeasonToFollowing(file.index)}
                            disabled={!file.included || file.season === null}
                          >
                            <ArrowDown className="h-2.5 w-2.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Apply to following</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>
                  <Input
                    type="number"
                    min={0}
                    value={file.season ?? ''}
                    onChange={(e) =>
                      updateFile(file.index, 'season', e.target.value ? parseInt(e.target.value) : null)
                    }
                    disabled={!file.included}
                    className="h-7 text-xs"
                    placeholder="S"
                  />
                </div>

                {/* Episode */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <Label className="text-[9px] text-muted-foreground">Episode</Label>
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-4 w-4"
                            onClick={() => applyEpisodeNumbering(file.index)}
                            disabled={!file.included}
                          >
                            <Play className="h-2.5 w-2.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>Auto-number from here</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  </div>
                  <Input
                    type="number"
                    min={0}
                    value={file.episode ?? ''}
                    onChange={(e) =>
                      updateFile(file.index, 'episode', e.target.value ? parseInt(e.target.value) : null)
                    }
                    disabled={!file.included}
                    className="h-7 text-xs"
                    placeholder="E"
                  />
                </div>

                {/* Episode End (for multi-episode files) */}
                <div className="space-y-1">
                  <Label className="text-[9px] text-muted-foreground">Ep. End</Label>
                  <Input
                    type="number"
                    min={0}
                    value={file.episodeEnd ?? ''}
                    onChange={(e) =>
                      updateFile(file.index, 'episodeEnd', e.target.value ? parseInt(e.target.value) : null)
                    }
                    disabled={!file.included}
                    className="h-7 text-xs"
                    placeholder="Multi"
                  />
                </div>
              </div>
            </Card>
          ))}
        </div>
      </ScrollArea>

      {/* Footer */}
      <div className="px-4 py-3 border-t bg-muted/30 flex-shrink-0">
        <div className="flex items-center justify-between">
          <div className="text-xs text-muted-foreground">
            {modifiedCount > 0 ? (
              <span className="text-primary font-medium">
                {modifiedCount} file{modifiedCount !== 1 ? 's' : ''} modified
              </span>
            ) : (
              'No changes'
            )}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={onCancel}>
              Cancel
            </Button>
            <Button size="sm" onClick={handleConfirm} className="bg-primary">
              <Check className="h-3 w-3 mr-1" />
              Confirm ({includedCount})
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
