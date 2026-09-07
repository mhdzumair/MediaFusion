import { useRef, useState, useEffect, useImperativeHandle, type Ref } from 'react'
import { useDropzone } from 'react-dropzone'
import { Link } from 'react-router-dom'
import { AlertCircle, FileUp, Loader2, Plus, Upload } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { contentImportApi, type ImportResponse, type TorrentAnalyzeResponse } from '@/lib/api/content-import'
import type { CatalogItemDetail } from '@/lib/api/catalog'
import { TorrentImportDialog } from '@/pages/ContentImport/components/TorrentImportDialog'
import type { TorrentImportFormData } from '@/pages/ContentImport/components/types'
import { cn } from '@/lib/utils'

type Source = { kind: 'torrent'; file: File } | { kind: 'nzb'; file: File } | { kind: 'magnet' | 'http'; url: string }
type QueuedFile = {
  source: Extract<Source, { file: File }>
  episodeTarget?: { season: number; episode: number }
}

export interface MediaStreamImportHandle {
  importFiles: (files: File[], season: number, episode: number) => void
}

interface Props {
  ref?: Ref<MediaStreamImportHandle>
  onBusyChange?: (busy: boolean) => void
  item: CatalogItemDetail
  selectedSeason?: number
  selectedEpisode?: number
  onImported: () => void
}

export function MediaStreamImport({ item, selectedSeason, selectedEpisode, onImported, ref, onBusyChange }: Props) {
  const [sourceOpen, setSourceOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [url, setUrl] = useState('')
  const [source, setSource] = useState<Source>()
  const [analysis, setAnalysis] = useState<TorrentAnalyzeResponse | null>(null)
  const [busy, setBusy] = useState<'analyze' | 'import' | null>(null)
  const [error, setError] = useState<string>()
  const [result, setResult] = useState<ImportResponse>()
  const [extractor, setExtractor] = useState<string>()
  const [prefill, setPrefill] = useState<Partial<TorrentImportFormData>>()
  const [fileQueue, setFileQueue] = useState<QueuedFile[]>([])
  const [activeQueueItem, setActiveQueueItem] = useState<QueuedFile>()
  const [batchPosition, setBatchPosition] = useState(0)
  const [batchTotal, setBatchTotal] = useState(0)
  const pending = useRef(false)
  const analyzeRef = useRef<
    ((next: Source, episodeTarget?: { season: number; episode: number }) => Promise<void>) | null
  >(null)
  const mediaType: 'movie' | 'series' = item.type === 'series' ? 'series' : 'movie'

  useEffect(() => {
    onBusyChange?.(!!busy || reviewOpen || sourceOpen)
  }, [busy, reviewOpen, sourceOpen, onBusyChange])
  const enqueueFiles = (files: File[], episodeTarget?: { season: number; episode: number }) => {
    const accepted = files
      .filter((file) => /\.(torrent|nzb)$/i.test(file.name) && file.size <= 20 * 1024 * 1024)
      .slice(0, 20)
      .map((file) => ({
        source: { kind: file.name.toLowerCase().endsWith('.nzb') ? 'nzb' : 'torrent', file } as QueuedFile['source'],
        episodeTarget,
      }))
    if (accepted.length === 0) {
      setError('Choose up to 20 .torrent or .nzb files, each up to 20 MB.')
      return
    }
    if (accepted.length !== files.length) {
      setError('Some files were skipped. Only .torrent and .nzb files up to 20 MB are accepted.')
    }
    setFileQueue((current) => [...current, ...accepted])
    setBatchTotal((current) => (activeQueueItem || current > 0 ? current + accepted.length : accepted.length))
  }

  useImperativeHandle(ref, () => ({
    importFiles(files, season, episode) {
      if (!pending.current && !reviewOpen && !sourceOpen) {
        enqueueFiles(files, { season, episode })
      }
    },
  }))

  async function analyze(next: Source, episodeTarget?: { season: number; episode: number }) {
    if (pending.current) return
    pending.current = true
    setBusy('analyze')
    setError(undefined)
    setResult(undefined)
    try {
      let data: TorrentAnalyzeResponse
      let detectedExtractor: string | undefined
      if (next.kind === 'torrent') {
        data = await contentImportApi.analyzeTorrent(next.file, mediaType, { target_media_id: item.id })
      } else if (next.kind === 'nzb') {
        const nzb = await contentImportApi.analyzeNZBFile(next.file, mediaType, item.id)
        if (nzb.status === 'error') throw new Error(nzb.error || 'Could not read this NZB file.')
        data = { ...nzb, status: 'success', matches: [], torrent_name: nzb.nzb_title }
      } else if (next.kind === 'magnet') {
        data = await contentImportApi.analyzeMagnet({
          magnet_link: next.url,
          meta_type: mediaType,
          target_media_id: item.id,
          resolve_files: true,
        })
      } else {
        const http = await contentImportApi.analyzeHTTP({ url: next.url, meta_type: mediaType })
        if (http.status === 'error') throw new Error(http.error || 'Could not analyze this stream URL.')
        detectedExtractor = http.detected_extractor
        data = {
          status: 'success',
          torrent_name: next.url,
          files: [{ index: 0, filename: item.title, size: 0 }],
        }
      }
      if (data.status === 'error') throw new Error(data.error || 'Could not read this source.')
      if (next.kind === 'torrent' || next.kind === 'magnet') {
        data = {
          ...data,
          files: data.files?.filter((file) =>
            /\.(mkv|mp4|avi|mov|wmv|m4v|ts|m2ts|webm|flv|divx|xvid)$/i.test(file.filename),
          ),
        }
        if (mediaType === 'series' && !data.files?.length) {
          throw new Error(
            'Episode files could not be resolved. Upload the .torrent file to review and map its episodes.',
          )
        }
      }
      setPrefill({
        contentType: mediaType,
        metaId: `mf:${item.id}`,
        title: item.title,
        // Only offer the selected episode for a single file; packs retain per-file detection.
        fileData:
          mediaType === 'series' &&
          (next.kind === 'http' || episodeTarget) &&
          data.files?.length === 1 &&
          (episodeTarget?.season ?? selectedSeason) !== undefined &&
          (episodeTarget?.episode ?? selectedEpisode) !== undefined
            ? [
                {
                  ...data.files[0],
                  season_number: episodeTarget?.season ?? selectedSeason!,
                  episode_number: episodeTarget?.episode ?? selectedEpisode!,
                  included: true,
                },
              ]
            : undefined,
      })
      setExtractor(detectedExtractor)
      setSource(next)
      setAnalysis(data)
      setSourceOpen(false)
      setReviewOpen(true)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not analyze this source. Please try again.')
      setActiveQueueItem(undefined)
      setFileQueue([])
      setBatchPosition(0)
      setBatchTotal(0)
    } finally {
      pending.current = false
      setBusy(null)
    }
  }

  analyzeRef.current = analyze

  useEffect(() => {
    if (activeQueueItem || fileQueue.length === 0 || pending.current || busy || reviewOpen || sourceOpen) return
    const [next, ...remaining] = fileQueue
    setFileQueue(remaining)
    setActiveQueueItem(next)
    void analyzeRef.current?.(next.source, next.episodeTarget)
  }, [activeQueueItem, busy, fileQueue, reviewOpen, sourceOpen])

  const dropzone = useDropzone({
    accept: { 'application/x-bittorrent': ['.torrent'], 'application/x-nzb': ['.nzb'] },
    maxFiles: 20,
    maxSize: 20 * 1024 * 1024,
    disabled: !!busy || reviewOpen,
    noClick: true,
    noKeyboard: true,
    onDropAccepted: (files) => enqueueFiles(files),
    onDropRejected: () => setError('Choose up to 20 .torrent or .nzb files, each up to 20 MB.'),
  })

  function analyzeUrl() {
    const value = url.trim()
    if (/^magnet:\?/i.test(value)) {
      void analyze({ kind: 'magnet', url: value })
      return
    }
    try {
      const parsed = new URL(value)
      if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error()
      void analyze({ kind: 'http', url: value })
    } catch {
      setError('Enter a valid magnet link or an HTTP/HTTPS stream URL.')
    }
  }

  async function importStream(form: TorrentImportFormData): Promise<ImportResponse> {
    if (!source || pending.current) throw new Error('An import is already in progress.')
    pending.current = true
    setBusy('import')
    setError(undefined)
    try {
      const files = form.fileData?.filter((file) => file.included)
      const common = {
        target_media_id: item.id,
        meta_type: mediaType,
        meta_id: `mf:${item.id}`,
        resolution: form.resolution,
        quality: form.quality,
        codec: form.codec,
        languages: form.languages?.join(','),
        is_anonymous: form.isAnonymous,
        anonymous_display_name: form.anonymousDisplayName,
      }
      let response: ImportResponse
      if (source.kind === 'http') {
        const episode = files?.[0]
        if (mediaType === 'series' && (!episode || episode.season_number == null || !episode.episode_number)) {
          throw new Error('Review the season and episode before importing.')
        }
        response = await contentImportApi.importHTTP({
          ...common,
          url: source.url,
          title: item.title,
          audio: form.audio?.join(','),
          hdr: form.hdr?.join(','),
          catalogs: form.catalogs?.join(','),
          extractor_name: extractor,
          season_number: episode?.season_number ?? undefined,
          episode_number: episode?.episode_number ?? undefined,
          episode_end: episode?.episode_end ?? undefined,
        })
      } else {
        const request = {
          ...common,
          audio: form.audio?.join(','),
          hdr: form.hdr?.join(','),
          catalogs: form.catalogs?.join(','),
          file_data: files ? JSON.stringify(files) : undefined,
          episode_name_parser: form.episodeNameParser,
          total_size: analysis?.total_size,
        }
        response =
          source.kind === 'nzb'
            ? await contentImportApi.importNZBFile({ ...request, nzb_file: source.file })
            : source.kind === 'torrent'
              ? await contentImportApi.importTorrent({ ...request, torrent_file: source.file })
              : await contentImportApi.importMagnet({ ...request, magnet_link: source.url })
      }
      if (response.status === 'success' || response.status === 'pending' || response.status === 'warning') {
        setReviewOpen(false)
        setResult(response)
        setActiveQueueItem(undefined)
        setBatchPosition((position) => position + 1)
        onImported()
      } else if (response.status === 'error') {
        throw new Error(response.message)
      }
      return response
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : 'Import failed. Please try again.'
      setError(message)
      throw cause
    } finally {
      pending.current = false
      setBusy(null)
    }
  }

  const errorMessage = error && (
    <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
      {error}
    </p>
  )

  return (
    <>
      <Card
        {...dropzone.getRootProps()}
        className={cn('border-dashed transition-colors', dropzone.isDragActive && 'border-primary bg-primary/5')}
      >
        <input {...dropzone.getInputProps()} aria-label="Upload a torrent or NZB file" />
        <CardContent className="space-y-3 p-4 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="rounded-xl bg-primary/10 p-3 text-primary">
                <Upload className="h-5 w-5" />
              </div>
              <div>
                <h2 className="font-semibold">Add a stream</h2>
                <p className="text-sm text-muted-foreground">
                  {dropzone.isDragActive
                    ? 'Drop your stream file here'
                    : 'Drop up to 20 .torrent or .nzb files here, or import a magnet or stream URL.'}
                </p>
                {mediaType === 'series' && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Single episodes, season packs and multiple seasons supported.
                  </p>
                )}
              </div>
            </div>
            <Button
              disabled={!!busy}
              onClick={() => {
                setError(undefined)
                setSourceOpen(true)
              }}
              className="shrink-0 rounded-xl"
            >
              {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
              {busy === 'analyze' ? 'Reading source…' : 'Import stream'}
            </Button>
          </div>
          {busy === 'analyze' && (
            <p role="status" className="text-sm text-muted-foreground">
              Reading stream details. Magnet file discovery can take up to a minute.
            </p>
          )}
          {!sourceOpen && !reviewOpen && errorMessage}
          {result && (
            <p
              role="status"
              className={cn(
                'text-sm',
                result.status === 'warning' ? 'text-amber-600 dark:text-amber-400' : 'text-foreground',
              )}
            >
              {result.message}
            </p>
          )}
        </CardContent>
      </Card>
      <Dialog
        open={sourceOpen}
        onOpenChange={(open) => {
          if (!busy) setSourceOpen(open)
        }}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>Import a stream</DialogTitle>
            <DialogDescription>
              Add to {item.title}
              {item.year ? ` (${item.year})` : ''}. Review quality{mediaType === 'series' ? ' and episode mapping' : ''}{' '}
              before importing.
            </DialogDescription>
          </DialogHeader>
          <Button variant="outline" className="h-24 w-full border-dashed" disabled={!!busy} onClick={dropzone.open}>
            <FileUp className="mr-2 h-5 w-5" />
            Choose .torrent or .nzb files
          </Button>
          <form
            className="space-y-3"
            onSubmit={(event) => {
              event.preventDefault()
              analyzeUrl()
            }}
          >
            <Label htmlFor="media-import-url">Magnet link or HTTP stream URL</Label>
            <Input
              id="media-import-url"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="magnet:?xt=… or https://…"
              disabled={!!busy}
              autoComplete="off"
            />
            {errorMessage}
            <Button type="submit" className="w-full" disabled={!!busy || !url.trim()}>
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              {busy ? 'Reading source…' : 'Review stream'}
            </Button>
          </form>
          <p className="text-xs text-muted-foreground">
            For YouTube and other sources, use{' '}
            <Link className="underline underline-offset-4" to="/dashboard/content-import">
              Content Import
            </Link>
            .
          </p>
        </DialogContent>
      </Dialog>
      <TorrentImportDialog
        open={reviewOpen}
        onOpenChange={(open) => {
          if (!busy) {
            setReviewOpen(open)
            setError(undefined)
            if (!open) {
              setActiveQueueItem(undefined)
              setFileQueue([])
              setBatchPosition(0)
              setBatchTotal(0)
            }
          }
        }}
        analysis={analysis}
        initialContentType={mediaType}
        prefillData={prefill}
        targetMedia={{ id: item.id, title: item.title, type: mediaType, poster: item.poster }}
        sourceLabel={source?.kind === 'http' ? 'HTTP Stream' : source?.kind === 'nzb' ? 'NZB' : 'Torrent'}
        currentIndex={batchTotal > 1 ? batchPosition : undefined}
        totalItems={batchTotal || 1}
        onImport={importStream}
        isImporting={busy === 'import'}
        importError={error}
      />
    </>
  )
}
