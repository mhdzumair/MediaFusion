/** Job payload fields consumed by listing-based Rust/Python scrapers. */
export interface ScraperPaginationPayload {
  pages?: number
  start_page?: number
  total_pages?: number
  scrape_all?: boolean
  search_keyword?: string
  scrap_catalog_id?: string
}

export interface ScraperPaginationFormState {
  pages: number
  startPage: number
  scrapeAll: boolean
  searchKeyword: string
  scrapCatalogId: string
}

const LISTING_PAGINATION_JOB_IDS = new Set([
  'tamilmv',
  'tamil_blasters',
  'arab_torrents',
  'formula_ext',
  'motogp_ext',
  'wwe_ext',
  'ufc_ext',
  'movies_tv_ext',
  'movierulz',
  'x1337',
  'thepiratebay',
  'rutor',
  'limetorrents',
  'yts',
  'nyaa',
  'animetosho',
  'subsplease',
  'animepahe',
  'bt4g',
  'bt52',
  'uindex',
])

const EXT_TO_JOB_IDS = new Set(['formula_ext', 'motogp_ext', 'wwe_ext', 'ufc_ext', 'movies_tv_ext'])

const ARAB_TORRENTS_JOB_ID = 'arab_torrents'

export function supportsListingPagination(jobId: string): boolean {
  return LISTING_PAGINATION_JOB_IDS.has(jobId)
}

export function supportsScrapeAll(jobId: string): boolean {
  return EXT_TO_JOB_IDS.has(jobId)
}

export function supportsArabTorrentsFilters(jobId: string): boolean {
  return jobId === ARAB_TORRENTS_JOB_ID
}

const PAGINATION_PAYLOAD_KEYS = [
  'pages',
  'start_page',
  'total_pages',
  'scrape_all',
  'search_keyword',
  'scrap_catalog_id',
] as const

export function defaultPaginationFormState(): ScraperPaginationFormState {
  return {
    pages: 1,
    startPage: 1,
    scrapeAll: false,
    searchKeyword: '',
    scrapCatalogId: 'all',
  }
}

function readPageCount(payload: Record<string, unknown>): number {
  const raw = payload.pages ?? payload.total_pages
  if (typeof raw === 'number' && Number.isFinite(raw)) {
    return Math.min(100, Math.max(1, Math.trunc(raw)))
  }
  return 1
}

export function parsePaginationFromPayload(payload: Record<string, unknown> | undefined): ScraperPaginationFormState {
  const base = payload ?? {}
  return {
    pages: readPageCount(base),
    startPage:
      typeof base.start_page === 'number' && Number.isFinite(base.start_page)
        ? Math.max(1, Math.trunc(base.start_page))
        : 1,
    scrapeAll: base.scrape_all === true,
    searchKeyword: typeof base.search_keyword === 'string' ? base.search_keyword : '',
    scrapCatalogId: typeof base.scrap_catalog_id === 'string' ? base.scrap_catalog_id : 'all',
  }
}

export function buildPaginationPayload(jobId: string, state: ScraperPaginationFormState): ScraperPaginationPayload {
  if (supportsScrapeAll(jobId) && state.scrapeAll) {
    return { scrape_all: true }
  }

  const payload: ScraperPaginationPayload = {}

  if (state.pages !== 1) {
    payload.pages = state.pages
  }
  if (state.startPage !== 1) {
    payload.start_page = state.startPage
  }

  if (supportsArabTorrentsFilters(jobId)) {
    const keyword = state.searchKeyword.trim()
    if (keyword) {
      payload.search_keyword = keyword
    }
    if (state.scrapCatalogId.trim() && state.scrapCatalogId !== 'all') {
      payload.scrap_catalog_id = state.scrapCatalogId.trim()
    }
  }

  return payload
}

export function mergePaginationIntoPayload(
  base: Record<string, unknown>,
  jobId: string,
  state: ScraperPaginationFormState,
): Record<string, unknown> {
  const rest = stripPaginationFromPayload(base)
  return {
    ...rest,
    ...buildPaginationPayload(jobId, state),
  }
}

export function stripPaginationFromPayload(base: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(base).filter(
      ([key]) => !PAGINATION_PAYLOAD_KEYS.includes(key as (typeof PAGINATION_PAYLOAD_KEYS)[number]),
    ),
  )
}

export function formatPaginationSummary(jobId: string, payload: Record<string, unknown> | undefined): string | null {
  if (!supportsListingPagination(jobId)) {
    return null
  }
  const state = parsePaginationFromPayload(payload)
  if (state.scrapeAll) {
    return 'All pages'
  }
  if (state.startPage === 1 && state.pages === 1) {
    return '1 page'
  }
  if (state.startPage === 1) {
    return `${state.pages} pages`
  }
  return `pages ${state.startPage}–${state.startPage + state.pages - 1}`
}
