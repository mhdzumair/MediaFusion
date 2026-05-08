import * as React from 'react'
import * as ScrollAreaPrimitive from '@radix-ui/react-scroll-area'
import { cn } from '@/lib/utils'

type ScrollAreaProps = React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root> & {
  /** Prevent parent containers from stealing wheel scroll while this area can still scroll. */
  containScroll?: boolean
}

const ScrollArea = React.forwardRef<React.ElementRef<typeof ScrollAreaPrimitive.Root>, ScrollAreaProps>(
  ({ className, children, containScroll = true, ...props }, ref) => {
    const viewportRef = React.useRef<HTMLDivElement | null>(null)

    const handleWheelCapture = React.useCallback(
      (event: React.WheelEvent<HTMLDivElement>) => {
        if (!containScroll) return
        const viewport = viewportRef.current
        if (!viewport) return

        const canScrollY = viewport.scrollHeight > viewport.clientHeight + 1
        const canScrollX = viewport.scrollWidth > viewport.clientWidth + 1
        if (!canScrollY && !canScrollX) return

        const isVerticalIntent = Math.abs(event.deltaY) >= Math.abs(event.deltaX)

        if (isVerticalIntent && canScrollY) {
          const atTop = viewport.scrollTop <= 0
          const atBottom = viewport.scrollTop + viewport.clientHeight >= viewport.scrollHeight - 1
          if (!(event.deltaY < 0 && atTop) && !(event.deltaY > 0 && atBottom)) {
            event.stopPropagation()
          }
          return
        }

        if (!isVerticalIntent && canScrollX) {
          const atLeft = viewport.scrollLeft <= 0
          const atRight = viewport.scrollLeft + viewport.clientWidth >= viewport.scrollWidth - 1
          if (!(event.deltaX < 0 && atLeft) && !(event.deltaX > 0 && atRight)) {
            event.stopPropagation()
          }
        }
      },
      [containScroll],
    )

    return (
      <ScrollAreaPrimitive.Root
        ref={ref}
        className={cn('relative min-h-0 min-w-0 overflow-hidden', className)}
        {...props}
      >
        <ScrollAreaPrimitive.Viewport
          ref={viewportRef}
          onWheelCapture={handleWheelCapture}
          className={cn(
            'h-full w-full rounded-[inherit] [&>div]:!block',
            containScroll ? 'overscroll-contain' : 'overscroll-auto',
          )}
        >
          {children}
        </ScrollAreaPrimitive.Viewport>
        <ScrollBar />
        <ScrollAreaPrimitive.Corner />
      </ScrollAreaPrimitive.Root>
    )
  },
)
ScrollArea.displayName = ScrollAreaPrimitive.Root.displayName

const ScrollBar = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.ScrollAreaScrollbar>
>(({ className, orientation = 'vertical', ...props }, ref) => (
  <ScrollAreaPrimitive.ScrollAreaScrollbar
    ref={ref}
    orientation={orientation}
    className={cn(
      'flex touch-none select-none transition-colors',
      orientation === 'vertical' && 'h-full w-2.5 border-l border-l-transparent p-[1px]',
      orientation === 'horizontal' && 'h-2.5 flex-col border-t border-t-transparent p-[1px]',
      className,
    )}
    {...props}
  >
    <ScrollAreaPrimitive.ScrollAreaThumb className="relative flex-1 rounded-full bg-border" />
  </ScrollAreaPrimitive.ScrollAreaScrollbar>
))
ScrollBar.displayName = ScrollAreaPrimitive.ScrollAreaScrollbar.displayName

export { ScrollArea, ScrollBar }
