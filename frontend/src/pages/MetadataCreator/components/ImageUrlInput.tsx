import { useCallback, useRef, useState } from 'react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { AlertCircle, ExternalLink, ImageIcon, Loader2, Upload, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { imageUploadApi } from '@/lib/api/image-upload'

type UploadImageAspect = 'poster' | 'backdrop' | 'logo'
type FitMode = 'cover' | 'contain'

interface ImageOptimizationPreset {
  width: number
  height: number
  quality: number
  fitMode: FitMode
}

const IMAGE_OPTIMIZATION_PRESETS: Record<UploadImageAspect, ImageOptimizationPreset> = {
  poster: {
    width: 600,
    height: 900,
    quality: 0.82,
    fitMode: 'cover',
  },
  backdrop: {
    width: 1280,
    height: 720,
    quality: 0.8,
    fitMode: 'cover',
  },
  logo: {
    width: 1000,
    height: 400,
    quality: 0.88,
    fitMode: 'contain',
  },
}

async function optimizeImageBeforeUpload(file: File, aspect: UploadImageAspect): Promise<File> {
  if (!file.type.startsWith('image/') || file.type === 'image/gif') {
    return file
  }

  const preset = IMAGE_OPTIMIZATION_PRESETS[aspect]
  const targetAspectRatio = preset.width / preset.height
  const objectUrl = URL.createObjectURL(file)
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const element = new Image()
      element.onload = () => resolve(element)
      element.onerror = () => reject(new Error('Failed to read image for optimization.'))
      element.src = objectUrl
    })

    const canvas = document.createElement('canvas')
    canvas.width = preset.width
    canvas.height = preset.height

    const context = canvas.getContext('2d')
    if (!context) {
      return file
    }

    if (preset.fitMode === 'cover') {
      const sourceAspectRatio = image.naturalWidth / image.naturalHeight
      let sourceWidth = image.naturalWidth
      let sourceHeight = image.naturalHeight
      let sourceX = 0
      let sourceY = 0

      // Center-crop to target aspect ratio before resizing.
      if (sourceAspectRatio > targetAspectRatio) {
        sourceWidth = Math.round(image.naturalHeight * targetAspectRatio)
        sourceX = Math.round((image.naturalWidth - sourceWidth) / 2)
      } else if (sourceAspectRatio < targetAspectRatio) {
        sourceHeight = Math.round(image.naturalWidth / targetAspectRatio)
        sourceY = Math.round((image.naturalHeight - sourceHeight) / 2)
      }

      context.drawImage(image, sourceX, sourceY, sourceWidth, sourceHeight, 0, 0, preset.width, preset.height)
    } else {
      // Keep the full logo visible and fit it into the target box.
      context.clearRect(0, 0, preset.width, preset.height)
      const scale = Math.min(preset.width / image.naturalWidth, preset.height / image.naturalHeight)
      const drawWidth = Math.round(image.naturalWidth * scale)
      const drawHeight = Math.round(image.naturalHeight * scale)
      const offsetX = Math.round((preset.width - drawWidth) / 2)
      const offsetY = Math.round((preset.height - drawHeight) / 2)
      context.drawImage(image, offsetX, offsetY, drawWidth, drawHeight)
    }

    const optimizedBlob = await new Promise<Blob | null>((resolve) => {
      canvas.toBlob(resolve, 'image/webp', preset.quality)
    })

    if (!optimizedBlob || optimizedBlob.size <= 0 || optimizedBlob.size >= file.size) {
      return file
    }

    const baseName = file.name.replace(/\.[^/.]+$/, '')
    return new File([optimizedBlob], `${baseName}.webp`, {
      type: 'image/webp',
      lastModified: Date.now(),
    })
  } catch {
    return file
  } finally {
    URL.revokeObjectURL(objectUrl)
  }
}

interface ImageUrlInputProps {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  aspectRatio?: 'poster' | 'backdrop' | 'logo'
  className?: string
  allowUpload?: boolean
}

export function ImageUrlInput({
  label,
  value,
  onChange,
  placeholder = 'https://example.com/image.jpg',
  aspectRatio = 'poster',
  className,
  allowUpload = false,
}: ImageUrlInputProps) {
  const [showPreview, setShowPreview] = useState(false)
  const [imageError, setImageError] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const handleChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      onChange(e.target.value)
      setImageError(false)
      setUploadError(null)
      setShowPreview(!!e.target.value)
    },
    [onChange],
  )

  const handleClear = useCallback(() => {
    onChange('')
    setShowPreview(false)
    setImageError(false)
    setUploadError(null)
  }, [onChange])

  const handleImageError = useCallback(() => {
    setImageError(true)
  }, [])

  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click()
  }, [])

  const handleFileSelect = useCallback(
    async (event: React.ChangeEvent<HTMLInputElement>) => {
      const selectedFile = event.target.files?.[0]
      if (!selectedFile) return

      setUploadError(null)
      setIsUploading(true)
      try {
        const fileForUpload = await optimizeImageBeforeUpload(selectedFile, aspectRatio)
        const response = await imageUploadApi.upload(fileForUpload)
        onChange(response.url)
        setImageError(false)
        setShowPreview(true)
      } catch (error) {
        setUploadError(error instanceof Error ? error.message : 'Failed to upload image.')
      } finally {
        setIsUploading(false)
        event.target.value = ''
      }
    },
    [aspectRatio, onChange],
  )

  return (
    <div className={cn('space-y-2', className)}>
      <Label>{label}</Label>
      <div className="flex gap-2">
        {allowUpload && (
          <input
            ref={fileInputRef}
            type="file"
            accept="image/jpeg,image/png,image/webp,image/gif"
            className="hidden"
            onChange={handleFileSelect}
          />
        )}
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
        {allowUpload && (
          <Button
            type="button"
            variant="outline"
            onClick={handleUploadClick}
            disabled={isUploading}
            title="Upload image"
          >
            {isUploading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                <span className="ml-2 hidden sm:inline">Uploading...</span>
              </>
            ) : (
              <>
                <Upload className="h-4 w-4" />
                <span className="ml-2 hidden sm:inline">Upload</span>
              </>
            )}
          </Button>
        )}
      </div>
      {uploadError && <p className="text-xs text-red-500">{uploadError}</p>}

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
