import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { ExternalPlayerMenu } from '@/components/player'
import type { StreamInfo } from '@/lib/api/catalog'

/** Proxy messages are playable streams even without MediaFusion database metadata. */
export function ProxyStreamCard({ stream, onPlay }: { stream: StreamInfo; onPlay: () => void }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="rounded-xl border p-4 space-y-3">
      <p className="font-medium">{stream.name}</p>
      <p className="text-sm whitespace-pre-line text-muted-foreground">{stream.description}</p>
      <div className="flex flex-wrap gap-2">
        <Button onClick={onPlay}>Play</Button>
        <Button
          variant="outline"
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(stream.url!)
              setCopied(true)
            } catch {
              setCopied(false)
            }
          }}
        >
          {copied ? 'Copied' : 'Copy URL'}
        </Button>
        <Button variant="outline" asChild>
          <a href={stream.url} target="_blank" rel="noopener noreferrer">
            Open externally
          </a>
        </Button>
        <ExternalPlayerMenu streamUrl={stream.url!} mediaflowUrl={stream.browser_url} />
      </div>
    </div>
  )
}
