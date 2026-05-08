import type { TaskRecord } from '@/lib/api'
import type { ScraperMetricsSummary } from '@/lib/api/metrics'

export function formatDate(dateStr: string | null): string {
  if (!dateStr) return '—'
  const date = new Date(dateStr)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString()
}

export function shortTaskId(taskId: string): string {
  return taskId.length > 10 ? `${taskId.slice(0, 10)}...` : taskId
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return 'N/A'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let value = bytes / 1024
  let unitIndex = 0
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024
    unitIndex += 1
  }
  return `${value.toFixed(2)} ${units[unitIndex]}`
}

export function formatDuration(seconds?: number | null): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return 'N/A'
  if (seconds < 1) return `${(seconds * 1000).toFixed(0)} ms`
  if (seconds < 60) return `${seconds.toFixed(1)} s`
  const mins = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${mins}m ${secs}s`
}

export function formatDurationMs(durationMs?: number | null): string {
  if (durationMs === null || durationMs === undefined || Number.isNaN(durationMs)) return '—'
  return formatDuration(durationMs / 1000)
}

export function formatMediaLabel(run: ScraperMetricsSummary): string {
  const title = run.meta_title || 'Unknown title'
  const meta = run.meta_id || 'Unknown meta'
  if (run.season !== null && run.season !== undefined && run.episode !== null && run.episode !== undefined) {
    return `${title} (${meta}) S${run.season}E${run.episode}`
  }
  return `${title} (${meta})`
}

export function summarizeTopMap(data: Record<string, number> | undefined, top: number = 3): string {
  if (!data || Object.keys(data).length === 0) return 'N/A'
  return Object.entries(data)
    .sort((a, b) => b[1] - a[1])
    .slice(0, top)
    .map(([key, value]) => `${key}:${value}`)
    .join(', ')
}

export function statusBadgeClass(status: string): string {
  if (status === 'running') return 'bg-blue-500/10 text-blue-500 border-blue-500/30'
  if (status === 'queued' || status === 'scheduled') return 'bg-primary/10 text-primary border-primary/30'
  if (status === 'cancel_requested') return 'bg-orange-500/10 text-orange-500 border-orange-500/30'
  if (status === 'success') return 'bg-emerald-500/10 text-emerald-500 border-emerald-500/30'
  if (status === 'cancelled') return 'bg-orange-500/10 text-orange-500 border-orange-500/30'
  if (status === 'error' || status === 'enqueue_failed') return 'bg-red-500/10 text-red-500 border-red-500/30'
  if (status === 'skipped') return 'bg-muted text-muted-foreground border-border'
  return 'bg-muted text-muted-foreground border-border'
}

export function isTaskCancellable(task: TaskRecord): boolean {
  if (task.cancellation_requested) {
    return false
  }
  return !['success', 'error', 'cancelled', 'skipped', 'enqueue_failed'].includes(task.status)
}

export function isTaskRetryable(task: TaskRecord): boolean {
  return ['error', 'cancelled', 'skipped', 'enqueue_failed'].includes(task.status)
}

export function getTaskStatusLabel(task: TaskRecord): string {
  if (task.cancellation_requested && task.status !== 'cancelled') {
    return `${task.status} (cancel requested)`
  }
  return task.status
}
