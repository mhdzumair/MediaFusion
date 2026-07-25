import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Hash, Loader2, Play, Plus, Radio, Trash2, Users, Bot } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { useToast } from '@/hooks/use-toast'
import { ApiRequestError } from '@/lib/api/client'
import { telegramApi, type TelegramDialog, type TelegramScrapingChannel } from '@/lib/api/telegram'

const MAX_CONCURRENT_PHOTOS = 2
let activePhotoLoads = 0
const photoWaiters: Array<() => void> = []

async function acquirePhotoSlot(): Promise<void> {
  if (activePhotoLoads < MAX_CONCURRENT_PHOTOS) {
    activePhotoLoads += 1
    return
  }
  await new Promise<void>((resolve) => {
    photoWaiters.push(resolve)
  })
  activePhotoLoads += 1
}

function releasePhotoSlot() {
  activePhotoLoads = Math.max(0, activePhotoLoads - 1)
  const next = photoWaiters.shift()
  if (next) {
    next()
  }
}

function DialogAvatar({ dialog }: { dialog: TelegramDialog }) {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const [visible, setVisible] = useState(false)
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    const node = rootRef.current
    if (!node || !dialog.has_photo) {
      return
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: '120px' },
    )
    observer.observe(node)
    return () => observer.disconnect()
  }, [dialog.has_photo])

  useEffect(() => {
    if (!visible || !dialog.has_photo) {
      return
    }

    let active = true
    let objectUrl: string | null = null

    const load = async () => {
      await acquirePhotoSlot()
      if (!active) {
        releasePhotoSlot()
        return
      }
      try {
        const blob = await telegramApi.getDialogPhotoBlob(dialog.id)
        if (!active) {
          return
        }
        objectUrl = URL.createObjectURL(blob)
        setSrc(objectUrl)
      } catch {
        if (active) {
          setSrc(null)
        }
      } finally {
        releasePhotoSlot()
      }
    }

    void load()

    return () => {
      active = false
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [visible, dialog.has_photo, dialog.id])

  const fallback =
    dialog.kind === 'group' ? (
      <Users className="h-5 w-5 text-muted-foreground" />
    ) : dialog.kind === 'bot' ? (
      <Bot className="h-5 w-5 text-muted-foreground" />
    ) : (
      <Radio className="h-5 w-5 text-muted-foreground" />
    )

  return (
    <div
      ref={rootRef}
      className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted"
    >
      {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : fallback}
    </div>
  )
}

function ChannelAvatar({ channelId, kind = 'channel' }: { channelId: string; kind?: string }) {
  const [src, setSrc] = useState<string | null>(null)

  useEffect(() => {
    let active = true
    let objectUrl: string | null = null

    const load = async () => {
      await acquirePhotoSlot()
      if (!active) {
        releasePhotoSlot()
        return
      }
      try {
        const blob = await telegramApi.getDialogPhotoBlob(channelId)
        if (!active) {
          return
        }
        objectUrl = URL.createObjectURL(blob)
        setSrc(objectUrl)
      } catch {
        if (active) {
          setSrc(null)
        }
      } finally {
        releasePhotoSlot()
      }
    }

    void load()

    return () => {
      active = false
      if (objectUrl) {
        URL.revokeObjectURL(objectUrl)
      }
    }
  }, [channelId])

  const fallback =
    kind === 'group' ? (
      <Users className="h-4 w-4 text-muted-foreground" />
    ) : (
      <Hash className="h-4 w-4 text-muted-foreground" />
    )

  return (
    <div className="flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full bg-muted">
      {src ? <img src={src} alt="" className="h-full w-full object-cover" /> : fallback}
    </div>
  )
}

const DEFAULT_SCRAPE_MESSAGE_LIMIT = 25

type ChannelScrapeSettings = {
  messageLimit: string
  scrapeAllMessages: boolean
}

function defaultChannelSettings(): ChannelScrapeSettings {
  return {
    messageLimit: String(DEFAULT_SCRAPE_MESSAGE_LIMIT),
    scrapeAllMessages: false,
  }
}

function parseMessageLimit(value: string): number {
  const parsed = Number.parseInt(value, 10)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_SCRAPE_MESSAGE_LIMIT
}

function formatApiError(error: unknown): string {
  if (error instanceof ApiRequestError) {
    if (typeof error.data.detail === 'string' && error.data.detail.length > 0) {
      return error.data.detail
    }
    if (typeof error.data.error === 'string' && error.data.error.length > 0) {
      return error.data.error
    }
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return 'An unexpected error occurred'
}

export function TelegramScrapingChannels() {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [search, setSearch] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [actionMessage, setActionMessage] = useState<string | null>(null)
  const [addingId, setAddingId] = useState<string | null>(null)
  const [scrapingChannelId, setScrapingChannelId] = useState<string | null>(null)
  const [scrapingAll, setScrapingAll] = useState(false)
  const [channelSettings, setChannelSettings] = useState<Record<string, ChannelScrapeSettings>>({})
  const [refreshStreamCountsUntil, setRefreshStreamCountsUntil] = useState<number | null>(null)

  const {
    data: config,
    isLoading: configLoading,
    refetch: refetchConfig,
  } = useQuery({
    queryKey: ['telegramConfig'],
    queryFn: () => telegramApi.getConfig(),
  })

  const {
    data: dialogsData,
    isLoading: dialogsLoading,
    error: dialogsError,
    refetch: refetchDialogs,
  } = useQuery({
    queryKey: ['telegramDialogs'],
    queryFn: () => telegramApi.listDialogs(80),
    enabled: config?.session_connected ?? false,
  })

  useEffect(() => {
    if (!refreshStreamCountsUntil) {
      return
    }
    const interval = window.setInterval(() => {
      void refetchConfig()
    }, 15_000)
    const timeout = window.setTimeout(
      () => {
        setRefreshStreamCountsUntil(null)
      },
      Math.max(0, refreshStreamCountsUntil - Date.now()),
    )
    return () => {
      window.clearInterval(interval)
      window.clearTimeout(timeout)
    }
  }, [refreshStreamCountsUntil, refetchConfig])

  const configuredIds = useMemo(
    () => new Set((config?.channels ?? []).map((channel) => channel.id)),
    [config?.channels],
  )

  const filteredDialogs = useMemo(() => {
    const dialogs = dialogsData?.dialogs ?? []
    const query = search.trim().toLowerCase()
    if (!query) {
      return dialogs
    }
    return dialogs.filter(
      (dialog) =>
        dialog.name.toLowerCase().includes(query) ||
        dialog.id.toLowerCase().includes(query) ||
        dialog.kind.toLowerCase().includes(query),
    )
  }, [dialogsData?.dialogs, search])

  const getChannelSettings = (channelId: string): ChannelScrapeSettings =>
    channelSettings[channelId] ?? defaultChannelSettings()

  const updateChannelSettings = (channelId: string, patch: Partial<ChannelScrapeSettings>) => {
    setChannelSettings((current) => ({
      ...current,
      [channelId]: {
        ...getChannelSettings(channelId),
        ...patch,
      },
    }))
  }

  const buildChannelLimitPayload = (channelId: string) => {
    const settings = getChannelSettings(channelId)
    if (settings.scrapeAllMessages) {
      return { scrape_all_messages: true }
    }
    return { message_limit: parseMessageLimit(settings.messageLimit) }
  }

  const buildChannelLimitsMap = (channels: TelegramScrapingChannel[]) =>
    Object.fromEntries(channels.map((channel) => [channel.id, buildChannelLimitPayload(channel.id)]))

  const notifyScrapeResult = (message: string, variant: 'default' | 'destructive' = 'default') => {
    setActionError(null)
    setActionMessage(message)
    toast({
      title: variant === 'destructive' ? 'Scrape not started' : 'Scrape queued',
      description: message,
      variant,
    })
  }

  const addMutation = useMutation({
    mutationFn: (dialog: TelegramDialog) => telegramApi.addChannel(dialog.id, dialog.name),
    onMutate: (dialog) => {
      setAddingId(dialog.id)
      setActionError(null)
      setActionMessage(null)
    },
    onSuccess: (_result, dialog) => {
      setActionMessage(`Added ${dialog.name} to your scraping list.`)
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
    },
    onError: (error) => {
      setActionError(formatApiError(error))
    },
    onSettled: () => {
      setAddingId(null)
    },
  })

  const removeMutation = useMutation({
    mutationFn: (channelId: string) => telegramApi.removeChannel(channelId),
    onSuccess: () => {
      setActionError(null)
      setActionMessage('Channel removed.')
      void queryClient.invalidateQueries({ queryKey: ['telegramConfig'] })
    },
    onError: (error) => {
      setActionError(formatApiError(error))
    },
  })

  const scrapeMutation = useMutation({
    mutationFn: (payload: {
      channel?: string
      scrape_all?: boolean
      message_limit?: number
      scrape_all_messages?: boolean
      channel_limits?: Record<string, Record<string, boolean | number>>
    }) => telegramApi.triggerScrape(payload),
    onSuccess: (result) => {
      notifyScrapeResult(result.message)
      setRefreshStreamCountsUntil(Date.now() + 10 * 60_000)
    },
    onError: (error) => {
      const message = formatApiError(error)
      setActionError(message)
      notifyScrapeResult(message, 'destructive')
    },
    onSettled: () => {
      setScrapingChannelId(null)
      setScrapingAll(false)
    },
  })

  const handleScrapeChannel = (channel: TelegramScrapingChannel) => {
    setScrapingChannelId(channel.id)
    scrapeMutation.mutate({
      channel: channel.id,
      scrape_all: false,
      ...buildChannelLimitPayload(channel.id),
    })
  }

  const handleScrapeAll = () => {
    const channels = config?.channels ?? []
    if (channels.length === 0) {
      return
    }
    setScrapingAll(true)
    scrapeMutation.mutate({
      scrape_all: true,
      channel_limits: buildChannelLimitsMap(channels),
    })
  }

  if (configLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading scraping channels...
      </div>
    )
  }

  if (!config?.session_connected) {
    return null
  }

  return (
    <div className="space-y-6 pt-4 border-t">
      <div className="space-y-2">
        <Label className="text-base">Scraping Channels</Label>
        <p className="text-sm text-muted-foreground">
          Channels, groups, and bot chats saved here are scraped using your connected Telegram session. Set how many
          recent messages to scan per source (default {DEFAULT_SCRAPE_MESSAGE_LIMIT}), or scrape the full history. When
          a scrape finishes, stream counts refresh automatically.
        </p>
      </div>

      {(config.channels ?? []).length > 0 && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={handleScrapeAll} disabled={scrapeMutation.isPending}>
            {scrapingAll ? (
              <>
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                Queueing scrape...
              </>
            ) : (
              <>
                <Play className="h-4 w-4 mr-2" />
                Scrape all channels
              </>
            )}
          </Button>
        </div>
      )}

      {(config.channels ?? []).length > 0 ? (
        <div className="space-y-2">
          <Label className="text-sm text-muted-foreground">Configured for scraping</Label>
          <div className="space-y-2">
            {(config.channels ?? []).map((channel: TelegramScrapingChannel) => {
              const settings = getChannelSettings(channel.id)
              const isScrapingThisChannel = scrapingChannelId === channel.id && scrapeMutation.isPending

              return (
                <div
                  key={channel.id}
                  className="flex flex-col gap-3 rounded-lg border border-border/60 bg-muted/20 p-3 lg:flex-row lg:items-center"
                >
                  <div className="flex min-w-0 flex-1 items-center gap-3">
                    <ChannelAvatar channelId={channel.id} />
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium truncate">{channel.name}</span>
                        {channel.is_public ? (
                          <Badge variant="outline">{channel.id}</Badge>
                        ) : (
                          <Badge variant="secondary">private</Badge>
                        )}
                      </div>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {channel.stream_count ?? 0} streams indexed
                        {!channel.is_public ? ` · ${channel.id}` : ''}
                      </p>
                    </div>
                  </div>

                  <div className="flex flex-wrap items-end gap-3">
                    <div className="space-y-1">
                      <Label htmlFor={`scrape-limit-${channel.id}`} className="text-xs">
                        Messages
                      </Label>
                      <Input
                        id={`scrape-limit-${channel.id}`}
                        type="number"
                        min={1}
                        value={settings.messageLimit}
                        disabled={settings.scrapeAllMessages || scrapeMutation.isPending}
                        onChange={(event) => updateChannelSettings(channel.id, { messageLimit: event.target.value })}
                        className="w-24 h-8"
                      />
                    </div>
                    <label className="flex items-center gap-2 text-xs pb-1">
                      <Checkbox
                        checked={settings.scrapeAllMessages}
                        disabled={scrapeMutation.isPending}
                        onCheckedChange={(checked) =>
                          updateChannelSettings(channel.id, { scrapeAllMessages: checked === true })
                        }
                      />
                      All messages
                    </label>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleScrapeChannel(channel)}
                      disabled={scrapeMutation.isPending}
                    >
                      {isScrapingThisChannel ? (
                        <>
                          <Loader2 className="h-3 w-3 mr-1 animate-spin" />
                          Queueing...
                        </>
                      ) : (
                        'Scrape'
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Remove ${channel.name}`}
                      onClick={() => removeMutation.mutate(channel.id)}
                      disabled={removeMutation.isPending || scrapeMutation.isPending}
                    >
                      <Trash2 className="h-4 w-4" />
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      ) : (
        <Alert>
          <AlertDescription>No scraping channels configured yet. Browse your Telegram chats below.</AlertDescription>
        </Alert>
      )}

      <div className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <Label className="text-sm text-muted-foreground">Browse your Telegram chats</Label>
          <Button variant="outline" size="sm" onClick={() => void refetchDialogs()} disabled={dialogsLoading}>
            {dialogsLoading ? 'Refreshing...' : 'Refresh list'}
          </Button>
        </div>
        <Input
          placeholder="Search by name, @username, or id..."
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />

        {dialogsError && (
          <Alert variant="destructive">
            <AlertDescription>{formatApiError(dialogsError)}</AlertDescription>
          </Alert>
        )}

        {dialogsLoading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground py-6">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading channels from your Telegram account...
          </div>
        ) : filteredDialogs.length === 0 ? (
          <p className="text-sm text-muted-foreground py-4">
            No matching channels, groups, or bots found. Join chats or start a bot in Telegram first, then refresh.
          </p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2">
            {filteredDialogs.map((dialog) => {
              const alreadyAdded = configuredIds.has(dialog.id)
              const isAdding = addingId === dialog.id
              return (
                <div
                  key={dialog.id}
                  className="flex items-center gap-3 rounded-lg border border-border/60 p-3 hover:bg-muted/20"
                >
                  <DialogAvatar dialog={dialog} />
                  <div className="min-w-0 flex-1">
                    <div className="font-medium truncate">{dialog.name}</div>
                    <div className="flex flex-wrap items-center gap-1 mt-1">
                      <Badge variant="outline" className="text-[10px] uppercase">
                        {dialog.kind}
                      </Badge>
                      {dialog.is_public ? (
                        <Badge variant="outline" className="text-[10px]">
                          {dialog.id}
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="text-[10px]">
                          private
                        </Badge>
                      )}
                    </div>
                  </div>
                  <Button
                    size="sm"
                    variant={alreadyAdded ? 'secondary' : 'default'}
                    disabled={alreadyAdded || isAdding}
                    onClick={() => addMutation.mutate(dialog)}
                  >
                    {alreadyAdded ? (
                      'Added'
                    ) : isAdding ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      <>
                        <Plus className="h-3 w-3 mr-1" />
                        Add
                      </>
                    )}
                  </Button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {actionMessage && (
        <Alert>
          <AlertDescription>{actionMessage}</AlertDescription>
        </Alert>
      )}

      {actionError && (
        <Alert variant="destructive">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      )}
    </div>
  )
}
