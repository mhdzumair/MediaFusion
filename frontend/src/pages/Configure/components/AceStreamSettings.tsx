import { useState, useEffect } from 'react'
import { Radio, Info } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { ProfileConfig } from './types'

interface AceStreamSettingsProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

export function AceStreamSettings({ config, onChange }: AceStreamSettingsProps) {
  const [enableAceStream, setEnableAceStream] = useState(config.eas ?? false)

  // Sync with config changes
  useEffect(() => {
    setEnableAceStream(config.eas ?? false)
  }, [config.eas])

  // Check if MediaFlow is configured
  const hasMediaFlow = !!(config.mfc?.pu && config.mfc?.ap)

  // Update parent config
  const handleEnableChange = (checked: boolean) => {
    setEnableAceStream(checked)
    onChange({
      ...config,
      eas: checked,
    })
  }

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Radio className="h-5 w-5" />
          AceStream
        </CardTitle>
        <CardDescription>
          Stream content via AceStream P2P protocol. Requires MediaFlow Proxy with AceEngine configured.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Enable AceStream Toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="enable-acestream" className="text-base">
              Enable AceStream Streams
            </Label>
            <p className="text-sm text-muted-foreground">Show AceStream streams in search results and catalogs</p>
          </div>
          <Switch id="enable-acestream" checked={enableAceStream} onCheckedChange={handleEnableChange} />
        </div>

        {/* MediaFlow Proxy Requirement */}
        {enableAceStream && !hasMediaFlow && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              <strong>MediaFlow Required:</strong> You must configure MediaFlow Proxy in the Services tab for AceStream
              playback to work. AceStream content is streamed through MediaFlow's AceEngine integration.
            </AlertDescription>
          </Alert>
        )}

        {enableAceStream && hasMediaFlow && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              AceStream content will be streamed through your MediaFlow Proxy with automatic transcoding for browser
              playback. Make sure AceEngine is running and accessible from your MediaFlow instance.
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
