import type { EditedFileLink, FileLink } from '@/components/stream/FileAnnotationDialog'

export interface FileAnnotationUpdate {
  file_id: number
  clear?: boolean
  season_number?: number | null
  episode_number?: number | null
  episode_end?: number | null
}

export interface BulkFileAnnotationRequest {
  stream_id: number
  media_id?: number
  updates: FileAnnotationUpdate[]
  reason?: string
}

export interface FileAnnotationResponse {
  applied: boolean
  updated: number
  failed: number
  errors: string[]
  suggestion_id?: string | null
  pending_review?: boolean
}

export function isEpisodeAnnotationModified(editedFile: EditedFileLink, originalFiles: FileLink[]): boolean {
  const original = originalFiles.find((f) => f.file_id === editedFile.file_id)
  if (!editedFile.included) {
    return (
      (original?.season_number ?? null) !== null ||
      (original?.episode_number ?? null) !== null ||
      (original?.episode_end ?? null) !== null
    )
  }
  return (
    editedFile.season_number !== (original?.season_number ?? null) ||
    editedFile.episode_number !== (original?.episode_number ?? null) ||
    (editedFile.episode_end ?? null) !== (original?.episode_end ?? null)
  )
}

export function buildFileAnnotationUpdates(
  editedFiles: EditedFileLink[],
  originalFiles: FileLink[],
  isModified: (file: EditedFileLink) => boolean,
): FileAnnotationUpdate[] {
  return editedFiles
    .filter(isModified)
    .map((editedFile): FileAnnotationUpdate | null => {
      if (!editedFile.included) {
        const original = originalFiles.find((f) => f.file_id === editedFile.file_id)
        const hadLink =
          (original?.season_number ?? null) !== null ||
          (original?.episode_number ?? null) !== null ||
          (original?.episode_end ?? null) !== null
        if (!hadLink) {
          return null
        }
        return { file_id: editedFile.file_id, clear: true }
      }

      return {
        file_id: editedFile.file_id,
        season_number: editedFile.season_number,
        episode_number: editedFile.episode_number,
        episode_end: editedFile.episode_end ?? null,
      }
    })
    .filter((update): update is FileAnnotationUpdate => update !== null)
}

export function buildEpisodeAnnotationUpdates(
  editedFiles: EditedFileLink[],
  originalFiles: FileLink[],
): FileAnnotationUpdate[] {
  return buildFileAnnotationUpdates(editedFiles, originalFiles, (file) =>
    isEpisodeAnnotationModified(file, originalFiles),
  )
}
