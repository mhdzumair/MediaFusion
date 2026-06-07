/** Build a content detail URL, optionally scoped to a single stream via query param. */
export function buildContentStreamUrl(
  mediaType: string | null | undefined,
  mediaId: number | null | undefined,
  streamId?: number | string | null,
): string | null {
  if (!mediaId || !mediaType) return null
  const routeType = mediaType === 'series' ? 'series' : 'movie'
  const base = `/dashboard/content/${routeType}/${mediaId}`
  if (streamId != null && streamId !== '') return `${base}?stream_id=${streamId}`
  return base
}

/** Meta id for Poster component — prefers IMDb for RPDB, falls back to mf: internal id. */
export function getPosterMetaId(imdbId?: string | null, mediaId?: number | null, fallback?: string): string {
  if (imdbId) return imdbId
  if (mediaId) return `mf:${mediaId}`
  return fallback || 'unknown'
}
