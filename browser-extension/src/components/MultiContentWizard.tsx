/**
 * Multi-Content Wizard Component
 * Handles file-to-metadata linking for movie collections and series packs
 */

import { useState, useCallback, useMemo } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Progress } from '@/components/ui/progress'
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { 
  TorrentAnalyzeResponse, 
  FileAnnotation,
  ImportMode,
  TorrentMatch,
} from '@/lib/types'
import { 
  Check, 
  FileVideo, 
  Search, 
  X, 
  Loader2,
  Film,
  Tv,
  Link2,
  AlertCircle,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'

interface MultiContentWizardProps {
  analysis: TorrentAnalyzeResponse
  importMode: ImportMode
  contentType: 'movie' | 'series'
  onComplete: (annotations: FileAnnotation[]) => void
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

// Get filename from path
function getFilenameOnly(fullPath: string): string {
  const parts = fullPath.split('/')
  return parts[parts.length - 1] || fullPath
}

// Parse title from filename
function parseFilenameForSearch(filename: string): string {
  const name = getFilenameOnly(filename)
  const withoutExt = name.replace(/\.[^/.]+$/, '')
  const cleaned = withoutExt.replace(/[._-]/g, ' ')
  
  return cleaned
    .replace(/\b(19|20)\d{2}\b/g, '')
    .replace(/\b(480p|720p|1080p|2160p|4k|uhd)\b/gi, '')
    .replace(/\b(bluray|webrip|web-dl|hdtv|dvdrip)\b/gi, '')
    .replace(/\b(x264|x265|hevc|avc)\b/gi, '')
    .replace(/\b(aac|ac3|dts)\b/gi, '')
    .replace(/\bs\d{1,2}e\d{1,2}\b/gi, '')
    .replace(/\[.*?\]/g, '')
    .replace(/\(.*?\)/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

interface FileWithMetadata extends FileAnnotation {
  searchQuery?: string
  linkedMeta?: TorrentMatch
}

export function MultiContentWizard({
  analysis,
  importMode: _importMode,
  contentType,
  onComplete,
  onCancel,
}: MultiContentWizardProps) {
  // Filter for video files only
  const videoFiles = useMemo(() => {
    const videoExtensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.m4v']
    return (analysis.files || [])
      .filter(f => videoExtensions.some(ext => f.filename.toLowerCase().endsWith(ext)))
      .filter(f => f.size > 50 * 1024 * 1024) // Filter files > 50MB
  }, [analysis.files])

  // Initialize file annotations
  const [files, setFiles] = useState<FileWithMetadata[]>(() => 
    videoFiles.map(f => ({
      index: f.index,
      filename: f.filename,
      size: f.size,
      searchQuery: parseFilenameForSearch(f.filename),
      skip: false,
    }))
  )

  const [currentFileIndex, setCurrentFileIndex] = useState(0)
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState<TorrentMatch[]>([])
  const [error, setError] = useState<string | null>(null)

  const currentFile = files[currentFileIndex]
  const linkedCount = files.filter(f => f.meta_id && !f.skip).length
  const progress = (linkedCount / files.length) * 100

  // Search for metadata
  const handleSearch = useCallback(async (query: string) => {
    if (query.length < 2) return

    setSearching(true)
    setError(null)

    try {
      // Use the analyze endpoint to search
      const result = await api.analyzeMagnet(
        `magnet:?xt=urn:btih:dummy&dn=${encodeURIComponent(query)}`,
        contentType
      )
      setSearchResults(result.matches || [])
    } catch (err) {
      setError('Search failed')
      setSearchResults([])
    } finally {
      setSearching(false)
    }
  }, [contentType])

  // Link file to metadata
  const handleLinkFile = useCallback((match: TorrentMatch) => {
    setFiles(prev => prev.map((f, i) => 
      i === currentFileIndex 
        ? { ...f, meta_id: match.id, title: match.title, linkedMeta: match }
        : f
    ))
    setSearchResults([])
    
    // Auto-advance to next unlinked file
    const nextUnlinked = files.findIndex((f, i) => i > currentFileIndex && !f.meta_id && !f.skip)
    if (nextUnlinked !== -1) {
      setCurrentFileIndex(nextUnlinked)
    }
  }, [currentFileIndex, files])

  // Toggle skip file
  const handleToggleSkip = useCallback((index: number) => {
    setFiles(prev => prev.map((f, i) => 
      i === index ? { ...f, skip: !f.skip } : f
    ))
  }, [])

  // Clear link
  const handleClearLink = useCallback((index: number) => {
    setFiles(prev => prev.map((f, i) => 
      i === index ? { ...f, meta_id: undefined, title: undefined, linkedMeta: undefined } : f
    ))
  }, [])

  // Complete wizard
  const handleComplete = useCallback(() => {
    const annotations: FileAnnotation[] = files
      .filter(f => f.meta_id || f.skip)
      .map(f => ({
        index: f.index,
        filename: f.filename,
        size: f.size,
        meta_id: f.meta_id,
        title: f.title,
        skip: f.skip,
      }))
    
    onComplete(annotations)
  }, [files, onComplete])

  return (
    <div className="space-y-4">
      {/* Progress */}
      <div className="space-y-2">
        <div className="flex justify-between text-sm">
          <span>Linking files to metadata</span>
          <span className="text-muted-foreground">
            {linkedCount} / {files.length} linked
          </span>
        </div>
        <Progress value={progress} />
      </div>

      {/* File List */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm">Files</CardTitle>
        </CardHeader>
        <CardContent className="max-h-[180px] overflow-y-auto space-y-1">
          {files.map((file, index) => (
            <button
              key={file.index}
              onClick={() => setCurrentFileIndex(index)}
              className={cn(
                "w-full flex items-center gap-2 p-2 rounded text-left text-sm transition-colors",
                index === currentFileIndex 
                  ? "bg-primary/10 border border-primary/50" 
                  : "hover:bg-secondary/50",
                file.skip && "opacity-50"
              )}
            >
              <FileVideo className={cn(
                "h-4 w-4 flex-shrink-0",
                file.meta_id ? "text-green-500" : "text-muted-foreground"
              )} />
              <span className="truncate flex-1" title={file.filename}>
                {getFilenameOnly(file.filename)}
              </span>
              {file.linkedMeta && (
                <span className="text-xs text-green-500 flex-shrink-0">
                  {file.linkedMeta.title}
                </span>
              )}
              {file.skip && (
                <span className="text-xs text-muted-foreground">Skipped</span>
              )}
            </button>
          ))}
        </CardContent>
      </Card>

      {/* Current File Editor */}
      {currentFile && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center justify-between">
              <span className="truncate">{getFilenameOnly(currentFile.filename)}</span>
              <span className="text-xs text-muted-foreground">
                {formatFileSize(currentFile.size)}
              </span>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {/* Link status */}
            {currentFile.linkedMeta ? (
              <div className="flex items-center gap-2 p-2 bg-green-500/10 rounded border border-green-500/30">
                <Check className="h-4 w-4 text-green-500" />
                <span className="text-sm flex-1 truncate">{currentFile.linkedMeta.title}</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleClearLink(currentFileIndex)}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ) : currentFile.skip ? (
              <div className="flex items-center gap-2 p-2 bg-muted rounded">
                <span className="text-sm text-muted-foreground">This file will be skipped</span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleToggleSkip(currentFileIndex)}
                >
                  Include
                </Button>
              </div>
            ) : (
              <>
                {/* Search input */}
                <div className="flex gap-2">
                  <Input
                    placeholder="Search for title..."
                    value={currentFile.searchQuery || ''}
                    onChange={(e) => {
                      const query = e.target.value
                      setFiles(prev => prev.map((f, i) =>
                        i === currentFileIndex ? { ...f, searchQuery: query } : f
                      ))
                    }}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        handleSearch(currentFile.searchQuery || '')
                      }
                    }}
                  />
                  <Button
                    size="icon"
                    onClick={() => handleSearch(currentFile.searchQuery || '')}
                    disabled={searching}
                  >
                    {searching ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <Search className="h-4 w-4" />
                    )}
                  </Button>
                </div>

                {/* Search results */}
                {searchResults.length > 0 && (
                  <div className="max-h-[150px] overflow-y-auto space-y-1">
                    {searchResults.map((match) => (
                      <button
                        key={match.id}
                        onClick={() => handleLinkFile(match)}
                        className="w-full flex items-center gap-2 p-2 rounded text-left hover:bg-secondary transition-colors"
                      >
                        <div className="w-8 h-10 rounded bg-muted flex-shrink-0 overflow-hidden">
                          {match.poster ? (
                            <img src={match.poster} alt="" className="w-full h-full object-cover" />
                          ) : (
                            <div className="w-full h-full flex items-center justify-center">
                              {contentType === 'movie' ? (
                                <Film className="h-4 w-4 text-muted-foreground" />
                              ) : (
                                <Tv className="h-4 w-4 text-muted-foreground" />
                              )}
                            </div>
                          )}
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium truncate">{match.title}</p>
                          <p className="text-xs text-muted-foreground">
                            {match.year} · {match.type}
                          </p>
                        </div>
                        <Link2 className="h-4 w-4 text-muted-foreground" />
                      </button>
                    ))}
                  </div>
                )}

                {/* Skip option */}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleToggleSkip(currentFileIndex)}
                  className="w-full"
                >
                  Skip this file
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Navigation */}
      <div className="flex gap-2">
        <Button variant="outline" onClick={onCancel} className="flex-1">
          Cancel
        </Button>
        <Button
          onClick={handleComplete}
          disabled={linkedCount === 0}
          className="flex-1"
        >
          <Check className="h-4 w-4" />
          Complete ({linkedCount} files)
        </Button>
      </div>
    </div>
  )
}
