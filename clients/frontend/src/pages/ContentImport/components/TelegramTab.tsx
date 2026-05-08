import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  Send,
  CheckCircle,
  XCircle,
  ExternalLink,
  Info,
  Magnet,
  FileVideo,
  Upload,
  Globe,
  Youtube,
  Radio,
  Newspaper,
} from 'lucide-react'
import type { TelegramFeatureConfig } from '@/lib/api/instance'

interface TelegramTabProps {
  telegram: TelegramFeatureConfig | undefined
}

const SUPPORTED_TYPES = [
  { icon: Magnet, label: 'Magnet Links', description: 'Paste any magnet link' },
  { icon: Upload, label: 'Torrent Files', description: 'Upload .torrent files directly' },
  { icon: FileVideo, label: 'Video Files', description: 'Forward or upload video files (min 25 MB)' },
  { icon: Globe, label: 'HTTP Links', description: 'Direct HTTP stream URLs' },
  { icon: Youtube, label: 'YouTube URLs', description: 'YouTube video links' },
  { icon: Radio, label: 'AceStream IDs', description: '40-character hex content IDs' },
  { icon: Newspaper, label: 'NZB Files', description: 'Usenet NZB download links' },
]

const IMPORT_STEPS = [
  { step: '1', title: 'Link your account', description: 'Send /login to the bot and follow the instructions.' },
  {
    step: '2',
    title: 'Send your content',
    description: 'Send a magnet link, torrent file, video file, or any supported content type to the bot.',
  },
  {
    step: '3',
    title: 'Choose media type',
    description: 'The bot will ask if this is a Movie, Series, or Sports content.',
  },
  {
    step: '4',
    title: 'Select metadata match',
    description: 'The bot searches for matching metadata. Pick the correct title or enter an external ID manually.',
  },
  {
    step: '5',
    title: 'Review and confirm',
    description: 'Review the detected metadata (resolution, quality, etc.), edit if needed, then confirm the import.',
  },
]

export function TelegramTab({ telegram }: TelegramTabProps) {
  const botConfigured = telegram?.bot_configured ?? false
  const botUsername = telegram?.bot_username

  const botUrl = botUsername ? `https://t.me/${botUsername}` : null

  return (
    <div className="space-y-6">
      {/* Bot Status Card */}
      <Card className="glass border-border/50">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Send className="h-5 w-5 text-blue-500" />
            Telegram Bot Import
          </CardTitle>
          <CardDescription>
            Import content through the Telegram bot by sending torrent files, magnet links, video files, and more
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Bot Status */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-muted/50">
            <div className="flex items-center gap-3">
              <div
                className={`h-2.5 w-2.5 rounded-full ${botConfigured ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`}
              />
              <div>
                <p className="text-sm font-medium">Bot Status</p>
                <p className="text-xs text-muted-foreground">
                  {botConfigured ? 'Bot is configured and ready' : 'Bot is not configured on this instance'}
                </p>
              </div>
            </div>
            <Badge variant={botConfigured ? 'default' : 'destructive'} className="gap-1">
              {botConfigured ? (
                <>
                  <CheckCircle className="h-3 w-3" />
                  Active
                </>
              ) : (
                <>
                  <XCircle className="h-3 w-3" />
                  Not Configured
                </>
              )}
            </Badge>
          </div>

          {/* Bot Link */}
          {botConfigured && botUrl && (
            <Button asChild variant="outline" className="w-full rounded-xl gap-2">
              <a href={botUrl} target="_blank" rel="noopener noreferrer">
                <Send className="h-4 w-4 text-blue-500" />
                Open @{botUsername} in Telegram
                <ExternalLink className="h-3.5 w-3.5 ml-auto" />
              </a>
            </Button>
          )}

          {!botConfigured && (
            <Alert>
              <XCircle className="h-4 w-4" />
              <AlertDescription>
                The Telegram bot is not configured on this server. Contact your administrator to set up the bot.
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* How to Import Guide */}
      {botConfigured && (
        <Card className="glass border-border/50">
          <CardHeader>
            <CardTitle className="text-base">How to Import via Telegram</CardTitle>
            <CardDescription className="text-sm">
              Follow these steps to import content using the Telegram bot
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ol className="space-y-4">
              {IMPORT_STEPS.map((item) => (
                <li key={item.step} className="flex gap-3">
                  <div className="flex-none flex items-center justify-center h-7 w-7 rounded-full bg-primary/10 text-primary text-sm font-semibold">
                    {item.step}
                  </div>
                  <div className="pt-0.5">
                    <p className="text-sm font-medium">{item.title}</p>
                    <p className="text-xs text-muted-foreground">{item.description}</p>
                  </div>
                </li>
              ))}
            </ol>
          </CardContent>
        </Card>
      )}

      {/* Supported Content Types */}
      <Card className="glass border-border/50">
        <CardHeader>
          <CardTitle className="text-base">Supported Content Types</CardTitle>
          <CardDescription className="text-sm">
            The Telegram bot can process all of the following content types
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-2">
            {SUPPORTED_TYPES.map((type) => (
              <div key={type.label} className="flex items-center gap-3 p-2.5 rounded-lg bg-muted/30">
                <type.icon className="h-4 w-4 text-muted-foreground flex-none" />
                <div className="min-w-0">
                  <p className="text-sm font-medium">{type.label}</p>
                  <p className="text-xs text-muted-foreground truncate">{type.description}</p>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Bot Commands Reference */}
      {botConfigured && (
        <div className="flex items-start gap-2 p-3 rounded-xl bg-muted/50">
          <Info className="h-4 w-4 text-muted-foreground mt-0.5 flex-none" />
          <div className="text-sm text-muted-foreground space-y-1">
            <p className="font-medium text-foreground">Bot Commands</p>
            <ul className="space-y-0.5 text-xs">
              <li>
                <code className="text-primary">/start</code> - Welcome message and quick start guide
              </li>
              <li>
                <code className="text-primary">/login</code> - Link your Telegram account to MediaFusion
              </li>
              <li>
                <code className="text-primary">/help</code> - Show help and supported content types
              </li>
              <li>
                <code className="text-primary">/status</code> - Check your account link status
              </li>
              <li>
                <code className="text-primary">/cancel</code> - Cancel the current operation
              </li>
            </ul>
          </div>
        </div>
      )}
    </div>
  )
}
