import { useState, useEffect } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Magnet, Loader2, ArrowRight, Info } from 'lucide-react'
import { useAnalyzeMagnet } from '@/hooks'
import type { TorrentAnalyzeResponse, TorrentMetaType } from '@/lib/api'
import type { ContentType } from '@/lib/constants'

// Helper to convert ContentType to TorrentMetaType (defaults to 'movie' for unsupported types like 'tv')
function toTorrentMetaType(contentType: ContentType): TorrentMetaType {
  if (contentType === 'tv') return 'movie'
  return contentType
}

interface MagnetTabProps {
  onAnalysisComplete: (analysis: TorrentAnalyzeResponse, magnetLink: string) => void
  onError: (message: string) => void
  contentType?: ContentType
  initialMagnet?: string
  autoAnalyze?: boolean
}

export function MagnetTab({
  onAnalysisComplete,
  onError,
  contentType = 'movie',
  initialMagnet,
  autoAnalyze = false,
}: MagnetTabProps) {
  const [magnetLink, setMagnetLink] = useState(initialMagnet || '')
  const [hasAutoAnalyzed, setHasAutoAnalyzed] = useState(false)
  const analyzeMagnet = useAnalyzeMagnet()

  const handleAnalyze = async (magnet?: string) => {
    const linkToAnalyze = magnet || magnetLink
    if (!linkToAnalyze.trim()) return

    try {
      const result = await analyzeMagnet.mutateAsync({
        magnet_link: linkToAnalyze,
        meta_type: toTorrentMetaType(contentType),
      })
      if (result.status === 'success' || result.matches) {
        onAnalysisComplete(result, linkToAnalyze)
      } else {
        onError(result.error || 'Failed to analyze magnet link')
      }
    } catch {
      onError('Failed to analyze magnet link')
    }
  }

  // Auto-analyze if initialMagnet is provided and autoAnalyze is true
  useEffect(() => {
    if (autoAnalyze && initialMagnet && !hasAutoAnalyzed) {
      setHasAutoAnalyzed(true)
      handleAnalyze(initialMagnet)
    }
  }, [autoAnalyze, initialMagnet, hasAutoAnalyzed])

  return (
    <Card className="glass border-border/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Magnet className="h-5 w-5 text-primary" />
          Import Magnet Link
        </CardTitle>
        <CardDescription>Paste a magnet link to analyze and import the torrent</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label htmlFor="magnet">Magnet Link</Label>
          <div className="flex gap-2">
            <Input
              id="magnet"
              placeholder="magnet:?xt=urn:btih:..."
              value={magnetLink}
              onChange={(e) => setMagnetLink(e.target.value)}
              className="font-mono text-sm rounded-xl"
            />
            <Button
              onClick={() => handleAnalyze()}
              disabled={!magnetLink.trim() || analyzeMagnet.isPending}
              className="rounded-xl bg-gradient-to-r from-primary to-primary/80"
            >
              {analyzeMagnet.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <ArrowRight className="mr-2 h-4 w-4" />
              )}
              Analyze
            </Button>
          </div>
        </div>
        <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5" />
          <p className="text-sm text-muted-foreground">
            The magnet link will be analyzed to extract metadata. You&apos;ll be able to review the details before
            importing.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

// Export for use in parent
export { type MagnetTabProps }
