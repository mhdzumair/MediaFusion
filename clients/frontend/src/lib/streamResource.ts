import type { ExternalIds, StreamInfo, StreamListResponse } from './api/catalog'

export function streamResourceId(mediaId: number, externalIds?: ExternalIds): string {
  for (const provider of ['imdb', 'tmdb', 'kitsu', 'tvdb', 'mal'] as const) {
    const id = externalIds?.[provider]
    if (id) return provider === 'imdb' || id.startsWith(`${provider}:`) ? id : `${provider}:${id}`
  }
  return `mf:${mediaId}`
}

export function streamResourcePath(type: string, id: string, season?: number, episode?: number): string {
  const resource =
    type === 'series' && season !== undefined && episode !== undefined ? `${id}:${season}:${episode}` : id
  return `/stream/${type}/${encodeURIComponent(resource).replace(/%3A/gi, ':')}.json`
}

export function isProxyStream(stream: StreamInfo): boolean {
  return stream.id == null && !stream.stream_type && Boolean(stream.url)
}

export function browserPlaybackUrl(
  url: string | undefined,
  config?: StreamListResponse['web_playback'],
): string | undefined {
  if (!url || !config?.proxy_url) return undefined
  try {
    const wrapped = new URL(`${config.proxy_url.replace(/\/$/, '')}/proxy/stream`)
    wrapped.searchParams.set('d', url)
    if (config.api_password) wrapped.searchParams.set('api_password', config.api_password)
    return wrapped.toString()
  } catch {
    return undefined
  }
}

/** Runs after the reverse proxy has rewritten url; never use a server-side copy. */
export function prepareStreamResponse(response: StreamListResponse): StreamListResponse {
  return {
    ...response,
    streams: response.streams.map((stream) => ({
      ...stream,
      browser_url:
        stream.stream_type === 'acestream' ? stream.url : browserPlaybackUrl(stream.url, response.web_playback),
    })),
  }
}
