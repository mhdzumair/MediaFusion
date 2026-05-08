import { useState, useEffect, useLayoutEffect, useRef, useCallback, type ReactNode } from 'react'
import { useWindowVirtualizer } from '@tanstack/react-virtual'
import type { ContentCardData } from './ContentCard'

function getColumnCount() {
  const w = window.innerWidth
  if (w >= 1024) return 6 // lg
  if (w >= 768) return 4 // md
  if (w >= 640) return 3 // sm
  return 2
}

// Column count per Tailwind breakpoint (must match ContentGrid's grid-cols-*)
function useColumnCount(): number {
  const [cols, setCols] = useState(getColumnCount)

  useEffect(() => {
    const update = () => setCols(getColumnCount())
    window.addEventListener('resize', update, { passive: true })
    return () => window.removeEventListener('resize', update)
  }, [])

  return cols
}

export interface VirtualContentGridProps {
  items: ContentCardData[]
  renderItem: (item: ContentCardData) => ReactNode
  onLoadMore?: () => void
  hasMore?: boolean
  loading?: boolean
  scrollTargetIndex?: number
}

export function VirtualContentGrid({
  items,
  renderItem,
  onLoadMore,
  hasMore,
  loading,
  scrollTargetIndex,
}: VirtualContentGridProps) {
  const cols = useColumnCount()
  const containerRef = useRef<HTMLDivElement>(null)

  // scrollMargin = distance from page top to container top.
  // Must be accurate so the window virtualizer knows which rows are in the viewport.
  // Use state (not ref) so the virtualizer re-renders once the DOM is measured.
  const [scrollMargin, setScrollMargin] = useState(0)
  useLayoutEffect(() => {
    if (containerRef.current) {
      setScrollMargin(containerRef.current.offsetTop)
    }
  }, [])

  const rowCount = Math.ceil(items.length / cols)

  // Estimate row height: poster aspect-[2/3] width = viewport/cols, plus ~56px text
  const estimateSize = useCallback(() => {
    const gap = 16 // gap-4 = 1rem = 16px
    const padding = 48 // page horizontal padding estimate
    const colWidth = (window.innerWidth - padding - gap * (cols - 1)) / cols
    return Math.round(colWidth * 1.5 + 56)
  }, [cols])

  const virtualizer = useWindowVirtualizer({
    count: rowCount,
    estimateSize,
    overscan: 2,
    scrollMargin,
  })

  // Trigger load more when the last visible row approaches the end.
  // Use the last virtual item's index (a stable primitive) as the dep,
  // not the full virtualItems array (which has a new reference every render).
  const lastVirtualIndex = virtualizer.getVirtualItems().at(-1)?.index ?? -1
  useEffect(() => {
    if (lastVirtualIndex >= rowCount - 2 && hasMore && !loading) {
      onLoadMore?.()
    }
  }, [lastVirtualIndex, rowCount, hasMore, loading, onLoadMore])

  // Scroll to a specific item index when requested
  useEffect(() => {
    if (scrollTargetIndex !== undefined && scrollTargetIndex >= 0) {
      const rowIndex = Math.floor(scrollTargetIndex / cols)
      virtualizer.scrollToIndex(rowIndex, { align: 'center', behavior: 'smooth' })
    }
  }, [scrollTargetIndex, cols, virtualizer])

  const virtualItems = virtualizer.getVirtualItems()

  return (
    <div ref={containerRef}>
      <div
        style={{
          height: virtualizer.getTotalSize(),
          width: '100%',
          position: 'relative',
        }}
      >
        {virtualItems.map((virtualRow) => {
          const rowStart = virtualRow.index * cols
          const rowItems = items.slice(rowStart, rowStart + cols)

          return (
            <div
              key={virtualRow.key}
              data-index={virtualRow.index}
              ref={virtualizer.measureElement}
              style={{
                position: 'absolute',
                top: 0,
                left: 0,
                width: '100%',
                transform: `translateY(${virtualRow.start - scrollMargin}px)`,
              }}
            >
              <div className="grid gap-4" style={{ gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))` }}>
                {rowItems.map((item) => renderItem(item))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
