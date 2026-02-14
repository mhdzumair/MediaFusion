import { useState } from 'react'
import { Send, Link, Unlink, Info, CheckCircle2, Loader2 } from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Switch } from '@/components/ui/switch'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import type { ProfileConfig } from './types'
import { useQuery } from '@tanstack/react-query'
import { getAppConfig } from '@/lib/api'
import { apiClient } from '@/lib/api/client'

interface TelegramConfig {
  enabled: boolean
  channels: Array<{
    id: string
    name: string
    username?: string
    chat_id?: string
    enabled: boolean
    priority: number
  }>
  use_global_channels: boolean
  global_channels_available: boolean
  global_channel_count: number
  account_linked: boolean
  telegram_user_id?: string
  linked_at?: string
}

interface TelegramSettingsProps {
  config: ProfileConfig
  onChange: (config: ProfileConfig) => void
}

export function TelegramSettings({ config, onChange }: TelegramSettingsProps) {
  // Fetch app config to check if Telegram is enabled on instance
  const { data: appConfig, isLoading: appConfigLoading } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
  })

  // Check if Telegram features are available on this instance
  const telegramEnabled = appConfig?.telegram?.enabled ?? false
  const botConfigured = appConfig?.telegram?.bot_configured ?? false

  // Fetch user's Telegram config (including link status)
  // Always fetch when Telegram is enabled on instance (not dependent on user toggle)
  const { data: telegramConfig, isLoading: telegramLoading } = useQuery<TelegramConfig>({
    queryKey: ['telegramConfig'],
    queryFn: () => apiClient.get<TelegramConfig>('/telegram/config'),
    enabled: telegramEnabled,
  })

  // Get linked status from API response
  const telegramLinked = telegramConfig?.account_linked ?? false

  // Local state
  const [enableTelegram, setEnableTelegram] = useState(config.ets ?? false)

  // Sync with config changes (during render, not in effect)
  const [prevEts, setPrevEts] = useState(config.ets)
  if (config.ets !== prevEts) {
    setPrevEts(config.ets)
    setEnableTelegram(config.ets ?? false)
  }

  // Update parent config
  const updateConfig = (newEnableTelegram: boolean) => {
    onChange({
      ...config,
      ets: newEnableTelegram,
    })
  }

  // Handlers
  const handleEnableTelegramChange = (checked: boolean) => {
    setEnableTelegram(checked)
    updateConfig(checked)
  }

  // Show loading state while fetching app config
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
          Stream content from Telegram channels. Requires MediaFlow Proxy with Telegram session configured.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Enable Telegram Streams Toggle */}
        <div className="flex items-center justify-between">
          <div className="space-y-0.5">
            <Label htmlFor="enable-telegram" className="text-base">
              Enable Telegram Streams
            </Label>
            <p className="text-sm text-muted-foreground">Show Telegram streams in search results and catalogs</p>
          </div>
          <Switch id="enable-telegram" checked={enableTelegram} onCheckedChange={handleEnableTelegramChange} />
        </div>

        {/* Account Link Status - Always show when Telegram is enabled on instance */}
        <div className="space-y-4 pt-4 border-t">
          <div className="flex items-center justify-between">
            <div className="space-y-0.5">
              <Label className="text-base flex items-center gap-2">
                Account Status
                {telegramLoading ? (
                  <Badge variant="outline" className="bg-muted text-muted-foreground">
                    <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                    Checking...
                  </Badge>
                ) : telegramLinked ? (
                  <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/30">
                    <CheckCircle2 className="h-3 w-3 mr-1" />
                    Linked
                  </Badge>
                ) : (
                  <Badge variant="outline" className="bg-yellow-500/10 text-yellow-500 border-yellow-500/30">
                    <Unlink className="h-3 w-3 mr-1" />
                    Not Linked
                  </Badge>
                )}
              </Label>
              <p className="text-sm text-muted-foreground">
                {telegramLinked
                  ? `Your Telegram account (ID: ${telegramConfig?.telegram_user_id}) is linked. Streams will be sent to your DM for playback.`
                  : 'Link your Telegram account to play streams. Videos will be sent to your DM.'}
              </p>
            </div>
          </div>

          {!telegramLinked && botConfigured && (
            <Alert>
              <Link className="h-4 w-4" />
              <AlertDescription className="flex items-center justify-between">
                <span>
                  To link your account, send <code className="bg-muted px-1 py-0.5 rounded">/login</code> to the
                  MediaFusion Telegram bot.
                </span>
              </AlertDescription>
            </Alert>
          )}

          {/* MediaFlow Proxy Requirement - only show when streams are enabled */}
          {enableTelegram && (
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription>
                <strong>Requirement:</strong> You must have MediaFlow Proxy configured with a Telegram session to stream
                content. The bot sends videos to your DM, and MediaFlow streams them via MTProto.
              </AlertDescription>
            </Alert>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
