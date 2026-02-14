import { useState, useCallback } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { ImageIcon, X, ExternalLink, AlertCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ImageUrlInputProps {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  aspectRatio?: 'poster' | 'backdrop' | 'logo'
  className?: string
}

export function ImageUrlInput({
  label,
  value,
  onChange,
  placeholder = 'https://example.com/image.jpg',
  aspectRatio = 'poster',
  className,
}: ImageUrlInputProps) {
  const [showPreview, setShowPreview] = useState(false)
  const [imageError, setImageError] = useState(false)

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onChange(e.target.value)
      setImageError(false)
      setShowPreview(!!e.target.value)
    },
    [onChange],
  )

  const handleClear = useCallback(() => {
    onChange('')
    setShowPreview(false)
    setImageError(false)
  }, [onChange])

  const handleImageError = useCallback(() => {
    setImageError(true)
  }, [])

  return (
    <div className={cn('space-y-2', className)}>
      <Label>{label}</Label>
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Input value={value} onChange={handleChange} placeholder={placeholder} className="pr-8" />
          {value && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="absolute right-1 top-1/2 -translate-y-1/2 h-6 w-6"
              onClick={handleClear}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>
        {value && (
          <Button
            type="button"
            variant="outline"
            size="icon"
            onClick={() => window.open(value, '_blank')}
            title="Open in new tab"
          >
            <ExternalLink className="h-4 w-4" />
          </Button>
        )}
      </div>

      {/* Preview */}
      {showPreview && value && (
        <div
          className={cn(
            'relative rounded-lg border border-border/50 overflow-hidden bg-muted/30',
            aspectRatio === 'poster' && 'w-24 h-36',
            aspectRatio === 'backdrop' && 'w-full h-32',
            aspectRatio === 'logo' && 'w-40 h-16',
          )}
        >
          {imageError ? (
            <div className="w-full h-full flex flex-col items-center justify-center text-muted-foreground">
              <AlertCircle className="h-6 w-6 text-primary mb-1" />
              <span className="text-xs">Failed to load</span>
            </div>
          ) : (
            <img src={value} alt="Preview" className="w-full h-full object-cover" onError={handleImageError} />
          )}
        </div>
      )}

      {!value && (
        <div
          className={cn(
            'rounded-lg border border-dashed border-border/50 flex items-center justify-center text-muted-foreground',
            aspectRatio === 'poster' && 'w-24 h-36',
            aspectRatio === 'backdrop' && 'w-full h-32',
            aspectRatio === 'logo' && 'w-40 h-16',
          )}
        >
          <ImageIcon className="h-6 w-6" />
        </div>
      )}
    </div>
  )
}
