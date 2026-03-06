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
  // Check if MediaFlow is configured
  const hasMediaFlow = Boolean(config.mfc?.pu?.trim() && config.mfc?.ap?.trim())
  const enableAceStream = hasMediaFlow ? (config.eas ?? false) : false

  // Update parent config
  const handleEnableChange = (checked: boolean) => {
    if (checked && !hasMediaFlow) {
      return
    }
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
            <p className="text-sm text-muted-foreground">
              Show AceStream streams in search results and catalogs
              {!hasMediaFlow ? ' (configure MediaFlow first)' : ''}
            </p>
          </div>
          <Switch
            id="enable-acestream"
            checked={enableAceStream}
            onCheckedChange={handleEnableChange}
            disabled={!hasMediaFlow}
          />
        </div>

        {!hasMediaFlow && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              Configure MediaFlow Proxy URL and API Password in <strong>External Services → MediaFlow</strong> to enable
              AceStream streams.
            </AlertDescription>
          </Alert>
        )}

        {/* AceStream Setup Requirement */}
        {enableAceStream && (
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription className="space-y-2">
              <p>
                <strong>AceStream setup required:</strong> AceStream playback needs MediaFlow Proxy and a reachable
                AceEngine instance.
              </p>
              <ol className="list-decimal list-inside text-sm text-muted-foreground space-y-1">
                <li>Run AceEngine and make it reachable from your MediaFlow container (default port: 6878).</li>
                <li>
                  In MediaFlow environment, set{' '}
                  <code className="bg-muted px-1 py-0.5 rounded">ENABLE_ACESTREAM=true</code>,{' '}
                  <code className="bg-muted px-1 py-0.5 rounded">ACESTREAM_HOST</code>, and{' '}
                  <code className="bg-muted px-1 py-0.5 rounded">ACESTREAM_PORT</code>.
                </li>
                <li>
                  Configure MediaFlow Proxy URL and API Password in <strong>External Services → MediaFlow</strong>.
                </li>
                <li>Restart MediaFlow after changing environment variables.</li>
              </ol>
              <p className="text-sm text-muted-foreground">
                {hasMediaFlow
                  ? 'MediaFlow is configured in this profile. Verify AceEngine connectivity if playback fails.'
                  : 'MediaFlow is not configured in this profile yet. Complete MediaFlow settings first.'}
              </p>
            </AlertDescription>
          </Alert>
        )}
      </CardContent>
    </Card>
  )
}
