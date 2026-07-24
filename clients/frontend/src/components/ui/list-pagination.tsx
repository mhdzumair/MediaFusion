import { useState } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export interface ListPaginationProps {
  currentPage: number
  totalPages: number
  totalItems: number
  pageSize: number
  onPageChange: (page: number) => void
  /** Prefix for the page-jump input id (avoids duplicate ids when two bars render). */
  inputId?: string
  className?: string
  /** When true, show the summary row even if everything fits on one page. */
  alwaysShowSummary?: boolean
  pageSizeOptions?: readonly number[]
  onPageSizeChange?: (size: number) => void
}

export function ListPagination({
  currentPage,
  totalPages,
  totalItems,
  pageSize,
  onPageChange,
  inputId = 'list-page-jump',
  className = '',
  alwaysShowSummary = false,
  pageSizeOptions,
  onPageSizeChange,
}: ListPaginationProps) {
  const [pageInput, setPageInput] = useState(String(currentPage))
  const [isEditingPage, setIsEditingPage] = useState(false)

  const showControls = totalPages > 1
  if (!alwaysShowSummary && !showControls) return null

  const startItem = totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1
  const endItem = Math.min(currentPage * pageSize, totalItems)

  const goToPage = (page: number) => {
    const clamped = Math.min(Math.max(page, 1), totalPages)
    onPageChange(clamped)
    setPageInput(String(clamped))
  }

  const handlePageInputSubmit = () => {
    const parsed = parseInt(pageInput, 10)
    if (Number.isFinite(parsed)) {
      goToPage(parsed)
    } else {
      setPageInput(String(currentPage))
    }
    setIsEditingPage(false)
  }

  const pages: (number | '...')[] = []
  if (totalPages <= 7) {
    for (let i = 1; i <= totalPages; i++) pages.push(i)
  } else if (currentPage <= 4) {
    for (let i = 1; i <= 5; i++) pages.push(i)
    pages.push('...')
    pages.push(totalPages)
  } else if (currentPage >= totalPages - 3) {
    pages.push(1)
    pages.push('...')
    for (let i = totalPages - 4; i <= totalPages; i++) pages.push(i)
  } else {
    pages.push(1)
    pages.push('...')
    for (let i = currentPage - 1; i <= currentPage + 1; i++) pages.push(i)
    pages.push('...')
    pages.push(totalPages)
  }

  const btnBase =
    'inline-flex items-center justify-center h-8 min-w-[2rem] px-2 text-sm rounded border transition-colors select-none'
  const btnActive = 'bg-primary text-primary-foreground border-primary font-medium'
  const btnInactive = 'bg-transparent text-foreground border-border hover:bg-accent cursor-pointer'
  const btnDisabled = 'opacity-40 cursor-not-allowed bg-transparent border-border text-muted-foreground'

  return (
    <div
      className={`flex flex-col gap-3 text-sm text-muted-foreground py-1 sm:flex-row sm:items-center sm:justify-between ${className}`}
    >
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <span>
          Showing {startItem.toLocaleString()}–{endItem.toLocaleString()} of {totalItems.toLocaleString()}
          {showControls && (
            <span className="hidden md:inline">
              {' '}
              · Page {currentPage.toLocaleString()} of {totalPages.toLocaleString()}
            </span>
          )}
        </span>
        {onPageSizeChange && pageSizeOptions && pageSizeOptions.length > 0 && (
          <Select value={String(pageSize)} onValueChange={(value) => onPageSizeChange(Number(value))}>
            <SelectTrigger className="h-8 w-[7.5rem] shrink-0 rounded-lg text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {pageSizeOptions.map((size) => (
                <SelectItem key={size} value={String(size)}>
                  {size} per page
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>
      {showControls && (
        <div className="flex flex-wrap items-center justify-center gap-1 sm:justify-end">
          <button
            className={`${btnBase} ${currentPage === 1 ? btnDisabled : btnInactive}`}
            disabled={currentPage === 1}
            onClick={() => goToPage(1)}
            title="First page"
          >
            <ChevronsLeft className="h-3.5 w-3.5" />
          </button>
          {totalPages > 10 && (
            <button
              className={`${btnBase} ${currentPage <= 10 ? btnDisabled : btnInactive}`}
              disabled={currentPage <= 10}
              onClick={() => goToPage(currentPage - 10)}
              title="Back 10 pages"
            >
              −10
            </button>
          )}
          <button
            className={`${btnBase} ${currentPage === 1 ? btnDisabled : btnInactive}`}
            disabled={currentPage === 1}
            onClick={() => goToPage(currentPage - 1)}
            title="Previous page"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>

          {pages.map((p, i) =>
            p === '...' ? (
              <span key={`ellipsis-${i}`} className="px-1 text-muted-foreground">
                …
              </span>
            ) : (
              <button
                key={p}
                className={`${btnBase} ${p === currentPage ? btnActive : btnInactive}`}
                onClick={() => goToPage(p as number)}
              >
                {p}
              </button>
            ),
          )}

          <button
            className={`${btnBase} ${currentPage === totalPages ? btnDisabled : btnInactive}`}
            disabled={currentPage === totalPages}
            onClick={() => goToPage(currentPage + 1)}
            title="Next page"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          {totalPages > 10 && (
            <button
              className={`${btnBase} ${currentPage + 10 > totalPages ? btnDisabled : btnInactive}`}
              disabled={currentPage + 10 > totalPages}
              onClick={() => goToPage(currentPage + 10)}
              title="Forward 10 pages"
            >
              +10
            </button>
          )}
          <button
            className={`${btnBase} ${currentPage === totalPages ? btnDisabled : btnInactive}`}
            disabled={currentPage === totalPages}
            onClick={() => goToPage(totalPages)}
            title="Last page"
          >
            <ChevronsRight className="h-3.5 w-3.5" />
          </button>

          <form
            className="flex items-center gap-1 ml-1"
            onSubmit={(e) => {
              e.preventDefault()
              handlePageInputSubmit()
            }}
          >
            <Input
              id={inputId}
              type="number"
              min={1}
              max={totalPages}
              value={isEditingPage ? pageInput : String(currentPage)}
              onChange={(e) => setPageInput(e.target.value)}
              onFocus={() => {
                setPageInput(String(currentPage))
                setIsEditingPage(true)
              }}
              onBlur={() => setIsEditingPage(false)}
              className="h-8 w-16 px-2 text-center text-sm"
              aria-label="Jump to page"
            />
            <button type="submit" className={`${btnBase} ${btnInactive} px-2.5`} title="Go to page">
              Go
            </button>
          </form>
        </div>
      )}
    </div>
  )
}
