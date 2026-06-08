import { apiClient } from './client'

function buildQuery(params: Record<string, string | number | boolean | undefined>): string {
  const query = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') {
      query.set(key, String(value))
    }
  }
  const text = query.toString()
  return text ? `?${text}` : ''
}

export async function connectEventStream<T>(
  url: string,
  params: Record<string, string | number | boolean | undefined>,
  eventName: string,
  onEvent: (payload: T) => void,
  onError: (error: Error) => void,
  signal?: AbortSignal,
): Promise<void> {
  const query = buildQuery(params)

  const headers: HeadersInit = {}
  const token = apiClient.getAccessToken()
  const apiKey = apiClient.getApiKey()
  if (token) {
    headers['Authorization'] = `Bearer ${token}`
  }
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`/api/v1${url}${query}`, {
    method: 'GET',
    headers,
    signal,
  })

  if (!response.ok) {
    throw new Error(`Event stream failed: HTTP ${response.status}`)
  }
  if (!response.body) {
    throw new Error('Event stream failed: empty response body')
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) {
        break
      }
      buffer += decoder.decode(value, { stream: true })

      const chunks = buffer.split('\n\n')
      buffer = chunks.pop() ?? ''
      for (const chunk of chunks) {
        const lines = chunk.split('\n')
        const eventLine = lines.find((line) => line.startsWith('event:'))
        const chunkEvent = eventLine?.slice(6).trim()
        if (chunkEvent && chunkEvent !== eventName) {
          continue
        }

        const dataLines = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trim())

        if (dataLines.length === 0) {
          continue
        }

        const data = dataLines.join('\n')
        try {
          const parsed = JSON.parse(data) as T
          onEvent(parsed)
        } catch (error) {
          onError(error instanceof Error ? error : new Error('Failed to parse event stream payload'))
        }
      }
    }
  } catch (error) {
    if (signal?.aborted) {
      return
    }
    onError(error instanceof Error ? error : new Error('Event stream connection closed'))
  } finally {
    reader.releaseLock()
  }
}
