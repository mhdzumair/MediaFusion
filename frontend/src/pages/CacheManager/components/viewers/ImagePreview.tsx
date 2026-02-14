import { useState, useEffect } from 'react'
import { Loader2, ImageIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatBytes } from '../../types'
import { fetchCacheImage } from '../../hooks/useCacheData'

interface ImagePreviewProps {
  cacheKey: string
  sizeBytes: number
  className?: string
}

export function ImagePreview({ cacheKey, sizeBytes, className }: ImagePreviewProps) {
  const [imageSrc, setImageSrc] = useState<string | null>(null)
  const [imageError, setImageError] = useState(false)
  const [loading, setLoading] = useState(true)
  
  useEffect(() => {
    let blobUrl: string | null = null
    let mounted = true
    
    const loadImage = async () => {
      try {
        setLoading(true)
        setImageError(false)
        
        blobUrl = await fetchCacheImage(cacheKey)
        
        if (mounted) {
          setImageSrc(blobUrl)
        }
      } catch (err) {
        console.error('Failed to load image:', err)
        if (mounted) {
          setImageError(true)
        }
      } finally {
        if (mounted) {
          setLoading(false)
        }
      }
    }
    
    loadImage()
    
    return () => {
      mounted = false
      if (blobUrl) {
        URL.revokeObjectURL(blobUrl)
      }
    }
  }, [cacheKey])
  
  return (
    <div className={cn("space-y-3", className)}>
      <div className="flex items-center justify-center p-6 rounded-xl border border-border/50 bg-muted/30 min-h-[300px]">
        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3">
            <Loader2 className="h-10 w-10 animate-spin text-muted-foreground" />
            <p className="text-sm text-muted-foreground">Loading image...</p>
          </div>
        ) : imageError || !imageSrc ? (
          <div className="text-center text-muted-foreground">
            <ImageIcon className="h-16 w-16 mx-auto mb-3 opacity-40" />
            <p className="text-base font-medium">Image preview not available</p>
            <p className="text-sm mt-1 opacity-70">Binary data: {formatBytes(sizeBytes)}</p>
          </div>
        ) : (
          <div className="text-center">
            <img
              src={imageSrc}
              alt="Cached image"
              className="max-w-full max-h-[500px] rounded-lg shadow-lg object-contain"
            />
            <p className="text-sm text-muted-foreground mt-4">
              {formatBytes(sizeBytes)}
            </p>
          </div>
        )}
      </div>
    </div>
  )
}

