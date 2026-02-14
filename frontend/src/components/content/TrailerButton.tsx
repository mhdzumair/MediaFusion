import { useState } from 'react'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Play, ChevronDown, Youtube, ExternalLink } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TrailerInfo } from '@/lib/api/catalog'

interface TrailerButtonProps {
  trailers: TrailerInfo[]
  title: string
  className?: string
}

export function TrailerButton({ trailers, title, className }: TrailerButtonProps) {
  const [selectedTrailer, setSelectedTrailer] = useState<TrailerInfo | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  if (!trailers || trailers.length === 0) {
    return null
  }

  const primaryTrailer = trailers.find(t => t.type === 'trailer') || trailers[0]

  const handlePlayTrailer = (trailer: TrailerInfo) => {
    setSelectedTrailer(trailer)
    setDialogOpen(true)
  }

  if (trailers.length === 1) {
    return (
      <>
        <Button
          variant="default"
          className={cn(
            'rounded-xl bg-gradient-to-r from-red-600 to-red-500 hover:from-red-700 hover:to-red-600 text-white shadow-lg',
            className
          )}
          onClick={() => handlePlayTrailer(primaryTrailer)}
        >
          <Play className="mr-2 h-4 w-4" />
          Watch Trailer
        </Button>

        <TrailerDialog
          trailer={selectedTrailer}
          title={title}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
        />
      </>
    )
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            variant="default"
            className={cn(
              'rounded-xl bg-gradient-to-r from-red-600 to-red-500 hover:from-red-700 hover:to-red-600 text-white shadow-lg',
              className
            )}
          >
            <Play className="mr-2 h-4 w-4" />
            Watch Trailer
            <ChevronDown className="ml-2 h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          {trailers.map((trailer, idx) => (
            <DropdownMenuItem
              key={`${trailer.key}-${idx}`}
              onClick={() => handlePlayTrailer(trailer)}
              className="cursor-pointer"
            >
              <Youtube className="mr-2 h-4 w-4 text-red-600" />
              <div className="flex-1 truncate">
                <span className="font-medium">
                  {trailer.name || `${trailer.type.charAt(0).toUpperCase() + trailer.type.slice(1)}`}
                </span>
                <span className="text-xs text-muted-foreground ml-2 capitalize">
                  ({trailer.type})
                </span>
              </div>
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      <TrailerDialog
        trailer={selectedTrailer}
        title={title}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
      />
    </>
  )
}

interface TrailerDialogProps {
  trailer: TrailerInfo | null
  title: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

function TrailerDialog({ trailer, title, open, onOpenChange }: TrailerDialogProps) {
  if (!trailer) return null

  const getYouTubeUrl = (key: string) => `https://www.youtube.com/watch?v=${key}`
  const getYouTubeEmbedUrl = (key: string) => `https://www.youtube.com/embed/${key}?autoplay=1&rel=0`

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[900px] p-0 overflow-hidden">
        <DialogHeader className="p-4 pb-0">
          <div className="flex items-center justify-between">
            <DialogTitle className="text-lg font-semibold">
              {trailer.name || `${title} - Trailer`}
            </DialogTitle>
            <a
              href={getYouTubeUrl(trailer.key)}
              target="_blank"
              rel="noopener noreferrer"
              className="text-muted-foreground hover:text-foreground transition-colors"
            >
              <ExternalLink className="h-5 w-5" />
            </a>
          </div>
        </DialogHeader>
        <div className="relative aspect-video w-full bg-black">
          {trailer.site === 'YouTube' ? (
            <iframe
              src={getYouTubeEmbedUrl(trailer.key)}
              title={trailer.name || `${title} - Trailer`}
              className="absolute inset-0 w-full h-full"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
            />
          ) : (
            <div className="absolute inset-0 flex items-center justify-center">
              <p className="text-muted-foreground">
                Unsupported video platform: {trailer.site}
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

