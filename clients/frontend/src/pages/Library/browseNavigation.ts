export const CONTENT_DETAIL_RETURN_URL_KEY = 'content_detail_return_url'

const DEFAULT_BROWSE_RETURN = '/dashboard/library?tab=browse'

export function getContentDetailReturnUrl(): string {
  try {
    return sessionStorage.getItem(CONTENT_DETAIL_RETURN_URL_KEY) || DEFAULT_BROWSE_RETURN
  } catch {
    return DEFAULT_BROWSE_RETURN
  }
}

export function saveContentDetailReturnUrl(pathname: string, search: string): void {
  try {
    sessionStorage.setItem(CONTENT_DETAIL_RETURN_URL_KEY, `${pathname}${search}`)
  } catch {
    // Ignore storage errors
  }
}
