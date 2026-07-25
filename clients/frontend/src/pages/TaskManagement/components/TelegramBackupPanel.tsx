import { useState } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Skeleton } from '@/components/ui/skeleton'
import { useRunTelegramBackupRestore, useRunTelegramBackupStore, useTelegramBackupStats, useToast } from '@/hooks'
import { ApiRequestError } from '@/lib/api'
import { Archive, Loader2, RefreshCw, Upload } from 'lucide-react'

export function TelegramBackupPanel() {
  const { toast } = useToast()
  const statsQuery = useTelegramBackupStats()
  const storeMutation = useRunTelegramBackupStore()
  const restoreMutation = useRunTelegramBackupRestore()

  const [batchSize, setBatchSize] = useState('25')
  const [messageLimit, setMessageLimit] = useState('500')
  const [onlyMissing, setOnlyMissing] = useState(true)
  const [captureFileIdOnStore, setCaptureFileIdOnStore] = useState(false)
  const [captureFileIdOnRestore, setCaptureFileIdOnRestore] = useState(true)

  const stats = statsQuery.data
  const backupReady = stats?.backup_channel_configured ?? false

  const runStore = async () => {
    try {
      const result = await storeMutation.mutateAsync({
        only_missing: onlyMissing,
        batch_size: Number.parseInt(batchSize, 10) || 25,
        capture_file_id: captureFileIdOnStore,
      })
      toast({
        title: 'Backup store queued',
        description: result.job_id
          ? `Job #${result.job_id} will copy streams from source channels into the backup channel.`
          : 'The backup store job was accepted.',
      })
    } catch (error) {
      const detail =
        error instanceof ApiRequestError
          ? error.message
          : error instanceof Error
            ? error.message
            : 'Failed to queue backup store job'
      toast({ title: 'Backup store failed', description: detail, variant: 'destructive' })
    }
  }

  const runRestore = async () => {
    try {
      const result = await restoreMutation.mutateAsync({
        message_limit: Number.parseInt(messageLimit, 10) || 500,
        capture_file_id: captureFileIdOnRestore,
      })
      toast({
        title: 'Backup restore queued',
        description: result.job_id
          ? `Job #${result.job_id} will scan the backup channel and relink database rows.`
          : 'The backup restore job was accepted.',
      })
    } catch (error) {
      const detail =
        error instanceof ApiRequestError
          ? error.message
          : error instanceof Error
            ? error.message
            : 'Failed to queue backup restore job'
      toast({ title: 'Backup restore failed', description: detail, variant: 'destructive' })
    }
  }

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-4">
            <div>
              <CardTitle className="flex items-center gap-2">
                <Archive className="h-5 w-5" />
                Telegram Backup Channel
              </CardTitle>
              <CardDescription>
                Copy indexed Telegram streams into <code>TELEGRAM_BACKUP_CHANNEL_ID</code>, or relink database rows
                after recreating the backup channel.
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void statsQuery.refetch()}
              disabled={statsQuery.isFetching}
            >
              {statsQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {statsQuery.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : stats ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">Total streams</p>
                <p className="text-2xl font-semibold">{stats.total_streams}</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">With file_id</p>
                <p className="text-2xl font-semibold">{stats.with_file_id}</p>
                <p className="text-xs text-muted-foreground">{stats.without_file_id} missing</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">With backup copy</p>
                <p className="text-2xl font-semibold">{stats.with_backup}</p>
                <p className="text-xs text-muted-foreground">{stats.without_backup} missing</p>
              </div>
              <div className="rounded-lg border p-3">
                <p className="text-xs text-muted-foreground">Backup channel</p>
                <div className="mt-1 flex items-center gap-2">
                  <Badge variant={backupReady ? 'default' : 'destructive'}>
                    {backupReady ? 'Configured' : 'Not configured'}
                  </Badge>
                </div>
                {stats.backup_channel_id ? (
                  <p className="mt-2 truncate text-xs text-muted-foreground">{stats.backup_channel_id}</p>
                ) : null}
              </div>
            </div>
          ) : null}

          {!backupReady ? (
            <p className="text-sm text-amber-500">
              Set <code>TELEGRAM_BACKUP_CHANNEL_ID</code> in your environment and restart the API/worker before running
              backup jobs.
            </p>
          ) : null}
        </CardContent>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Upload className="h-4 w-4" />
              Store to backup channel
            </CardTitle>
            <CardDescription>
              Copies media from each stream&apos;s source <code>chat_id/message_id</code> into the backup channel
              without a forward tag. Use this after configuring a new backup channel.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="backup-batch-size">Batch size</Label>
                <Input
                  id="backup-batch-size"
                  value={batchSize}
                  onChange={(e) => setBatchSize(e.target.value)}
                  inputMode="numeric"
                />
              </div>
              <div className="flex items-center justify-between rounded-lg border p-3">
                <div>
                  <Label htmlFor="only-missing">Only missing backup</Label>
                  <p className="text-xs text-muted-foreground">Skip rows that already have backup coords</p>
                </div>
                <Switch id="only-missing" checked={onlyMissing} onCheckedChange={setOnlyMissing} />
              </div>
            </div>
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div>
                <Label htmlFor="capture-on-store">Capture file_id</Label>
                <p className="text-xs text-muted-foreground">
                  Uses a temporary bot copy inside the backup channel, then deletes it
                </p>
              </div>
              <Switch id="capture-on-store" checked={captureFileIdOnStore} onCheckedChange={setCaptureFileIdOnStore} />
            </div>
            <Button onClick={() => void runStore()} disabled={!backupReady || storeMutation.isPending}>
              {storeMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Upload className="mr-2 h-4 w-4" />
              )}
              Queue backup store job
            </Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <RefreshCw className="h-4 w-4" />
              Restore from backup channel
            </CardTitle>
            <CardDescription>
              Scans recent messages in the backup channel and relinks matching database rows by{' '}
              <code>file_unique_id</code> or filename caption.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="restore-message-limit">Messages to scan</Label>
              <Input
                id="restore-message-limit"
                value={messageLimit}
                onChange={(e) => setMessageLimit(e.target.value)}
                inputMode="numeric"
              />
            </div>
            <div className="flex items-center justify-between rounded-lg border p-3">
              <div>
                <Label htmlFor="capture-on-restore">Capture file_id</Label>
                <p className="text-xs text-muted-foreground">
                  Refreshes bot <code>file_id</code> via a temporary in-channel copy in the backup channel
                </p>
              </div>
              <Switch
                id="capture-on-restore"
                checked={captureFileIdOnRestore}
                onCheckedChange={setCaptureFileIdOnRestore}
              />
            </div>
            <Button onClick={() => void runRestore()} disabled={!backupReady || restoreMutation.isPending}>
              {restoreMutation.isPending ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="mr-2 h-4 w-4" />
              )}
              Queue backup restore job
            </Button>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
