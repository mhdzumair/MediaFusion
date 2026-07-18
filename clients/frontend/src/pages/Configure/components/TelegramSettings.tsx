import { useState } from 'react'
import { Send, Info, Loader2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { ProfileConfig } from './types'
import { useQuery } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'

interface TelegramSettingsProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

export function TelegramSettings({ config, onChange }: TelegramSettingsProps) {
  const { data: appConfig, isLoading: appConfigLoading } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
  })

  const telegramEnabled = appConfig?.telegram?.enabled ?? false
  const [enableTelegram, setEnableTelegram] = useState(config.ets ?? false)

  const [prevEts, setPrevEts] = useState(config.ets)
  if (config.ets !== prevEts) {
    setPrevEts(config.ets)
    setEnableTelegram(config.ets ?? false)
  }

  const handleEnableTelegramChange = (checked: boolean) => {
    setEnableTelegram(checked)
    onChange({
      ...config,
      ets: checked,
    })
  }

  if (appConfigLoading) {
    return (
      <Card className="border-border/50 bg-card/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-5 w-5" />
            Telegram Streams
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-2 text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading...
          </div>
        </CardContent>
      </Card>
    )
  }

  if (!telegramEnabled) {
    return (
      <Card className="border-border/50 bg-card/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-5 w-5" />
            Telegram Streams
          </CardTitle>
          <CardDescription>Stream content from Telegram channels</CardDescription>
        </CardHeader>
        <CardContent>
          <Alert>
            <Info className="h-4 w-4" />
            <AlertDescription>
              Telegram streaming is not enabled on this instance. Contact the administrator if you'd like this feature.
            </AlertDescription>
          </Alert>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card className="border-border/50 bg-card/50">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Send className="h-5 w-5" />
          Telegram Streams
        </CardTitle>
        <CardDescription>
          Profile setting for showing Telegram streams in catalogs. Connect your Telegram account under Integrations.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="enable-telegram" className="text-base">
              Enable Telegram Streams
            </Label>
            <p className="text-sm text-muted-foreground">Show Telegram streams in search results and catalogs</p>
          </div>
          <Switch id="enable-telegram" checked={enableTelegram} onCheckedChange={handleEnableTelegramChange} />
        </div>

        <Alert>
          <Info className="h-4 w-4" />
          <AlertDescription>
            Scraping session, bot playback, and channel management are configured on the Integrations page (per user
            account).
          </AlertDescription>
        </Alert>
      </CardContent>
    </Card>
  )
}
