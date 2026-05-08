import { useState, useMemo } from 'react'
import { Copy, Check, Binary } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'
import { formatBytes } from '../../types'
import { ImagePreview } from './ImagePreview'

interface StringViewerProps {
  value: unknown
  isBinary: boolean
  cacheKey?: string
  className?: string
}

export function StringViewer({ value, isBinary, cacheKey, className }: StringViewerProps) {
  const [copied, setCopied] = useState(false)

  // Format the value for display (must be before any conditional returns)
  const { formattedValue, isJson } = useMemo(() => {
    if (value === null || value === undefined) {
      return { formattedValue: 'null', isJson: false }
    }

    // If already an object, stringify it
    if (typeof value === 'object') {
      return { formattedValue: JSON.stringify(value, null, 2), isJson: true }
    }

    // Try to parse string as JSON
    const strValue = String(value)
    try {
      const parsed = JSON.parse(strValue)
      return { formattedValue: JSON.stringify(parsed, null, 2), isJson: true }
    } catch {
      return { formattedValue: strValue, isJson: false }
    }
  }, [value])

  // Check if it's binary data with image preview
  if (isBinary && typeof value === 'object' && value !== null && '_binary' in value && cacheKey) {
    const binaryData = value as { _binary: boolean; size_bytes: number; preview_base64: string; message: string }
    // Check if it looks like an image key
    if (
      cacheKey.endsWith('.jpg') ||
      cacheKey.endsWith('.jpeg') ||
      cacheKey.endsWith('.png') ||
      cacheKey.endsWith('.gif') ||
      cacheKey.endsWith('.webp')
    ) {
      return <ImagePreview cacheKey={cacheKey} sizeBytes={binaryData.size_bytes} className={className} />
    }
    // Non-image binary data - show info
    return (
      <div className={cn('rounded-xl border border-border/50 bg-muted/30 p-8 text-center', className)}>
        <Binary className="h-16 w-16 mx-auto mb-4 text-muted-foreground opacity-40" />
        <p className="text-base font-medium">Binary Data</p>
        <p className="text-sm text-muted-foreground mt-1">{formatBytes(binaryData.size_bytes)}</p>
      </div>
    )
  }

  const copyValue = async () => {
    await navigator.clipboard.writeText(formattedValue)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className={cn('space-y-3', className)}>
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={copyValue} className="gap-2">
          {copied ? (
            <>
              <Check className="h-4 w-4 text-emerald-400" />
              Copied!
            </>
          ) : (
            <>
              <Copy className="h-4 w-4" />
              Copy Value
            </>
          )}
        </Button>
      </div>

      <div className="rounded-xl border border-border/50 overflow-hidden bg-card/50">
        <ScrollArea className="h-[400px]">
          <pre
            className={cn(
              'p-4 text-sm font-mono whitespace-pre-wrap break-all leading-relaxed',
              isJson && 'text-emerald-400',
            )}
          >
            {formattedValue}
          </pre>
        </ScrollArea>
      </div>
    </div>
  )
}
