const ANONYMOUS_DISPLAY_NAME_KEY = 'mediafusion_anonymous_display_name'

export function getStoredAnonymousDisplayName(): string {
  if (typeof window === 'undefined') return ''
  return localStorage.getItem(ANONYMOUS_DISPLAY_NAME_KEY) || ''
}

export function saveAnonymousDisplayName(value: string): void {
  if (typeof window === 'undefined') return
  localStorage.setItem(ANONYMOUS_DISPLAY_NAME_KEY, value)
}

export function normalizeAnonymousDisplayName(value: string): string | undefined {
  const normalized = value.trim().replace(/\s+/g, ' ')
  return normalized.length > 0 ? normalized : undefined
}
