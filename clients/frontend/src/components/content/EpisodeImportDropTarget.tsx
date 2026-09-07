import { useState, type ReactNode } from 'react'
import { useDropzone } from 'react-dropzone'
import { cn } from '@/lib/utils'

interface Props {
  season: number
  episode: number
  disabled?: boolean
  onImport?: (files: File[], season: number, episode: number) => void
  children: (openFilePicker: () => void) => ReactNode
}

export function EpisodeImportDropTarget({ season, episode, disabled, onImport, children }: Props) {
  const [error, setError] = useState<string>()
  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    accept: { 'application/x-bittorrent': ['.torrent'], 'application/x-nzb': ['.nzb'] },
    maxFiles: 20,
    maxSize: 20 * 1024 * 1024,
    disabled: disabled || !onImport,
    noClick: true,
    noKeyboard: true,
    onDropAccepted: (files) => {
      setError(undefined)
      if (files.length > 0) onImport?.(files, season, episode)
    },
    onDropRejected: () => setError('Choose up to 20 .torrent or .nzb files, each up to 20 MB.'),
  })

  return (
    <div
      {...getRootProps()}
      data-episode-import={`${season}-${episode}`}
      className={cn('rounded-xl transition-colors', isDragActive && 'bg-primary/10 ring-2 ring-primary')}
    >
      <input {...getInputProps()} aria-label={`Import file for season ${season}, episode ${episode}`} />
      {children(open)}
      {isDragActive && (
        <p role="status" className="px-3 pb-2 text-xs font-medium text-primary">
          Drop files to import for S{season} E{episode}. Packs keep their per-file episode mappings.
        </p>
      )}
      {error && (
        <p role="alert" className="px-3 pb-2 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
