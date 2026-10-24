import type { TorrentAnalyzeResponse } from '../../../lib/api/content-import'
import type { FileAnnotation } from '../components/types'

export function collectionFiles(analysis: TorrentAnalyzeResponse): FileAnnotation[] {
  const source = analysis.files?.length
    ? analysis.files
    : (analysis.resolved?.files.map((file, index) => ({ ...file, index })) ?? [])
  return source
    .map((file, position) => ({
      filename: file.path || ('filename' in file ? file.filename : '') || '',
      size: file.size,
      index: file.index ?? position,
      season_number: null,
      episode_number: null,
      included: true,
    }))
    .filter((file) => /\.(mkv|mp4|avi|mov|wmv|m4v|webm|ts|m2ts|mpg|mpeg|flv|vob|ogv)$/i.test(file.filename))
    .sort((a, b) => a.filename.localeCompare(b.filename, undefined, { numeric: true }))
}
