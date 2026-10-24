import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { RESOLUTION_OPTIONS } from '@/lib/constants'
import { useCreateStreamSuggestion } from '@/hooks/useStreamSuggestions'

export function StreamResolutionEditor({
  streamId,
  currentResolution,
}: {
  streamId: number
  currentResolution?: string | null
}) {
  const normalized =
    RESOLUTION_OPTIONS.find((option) => option.value.toLowerCase() === currentResolution?.toLowerCase())?.value ??
    (currentResolution === '2160p' ? '4K' : '')
  const [resolution, setResolution] = useState(normalized)
  const [submitted, setSubmitted] = useState(normalized)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const suggestion = useCreateStreamSuggestion()
  const queryClient = useQueryClient()

  const save = async () => {
    setError('')
    setMessage('')
    try {
      const result = await suggestion.mutateAsync({
        streamId,
        data: {
          suggestion_type: 'field_correction',
          field_name: 'resolution',
          current_value: currentResolution || undefined,
          suggested_value: resolution,
          reason: 'Correct torrent resolution while annotating files',
        },
      })
      setSubmitted(resolution)
      setMessage(result.status === 'auto_approved' ? 'Resolution saved.' : 'Resolution submitted for review.')
      if (result.status === 'auto_approved') await queryClient.invalidateQueries({ queryKey: ['catalog'] })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Failed to save resolution')
    }
  }

  return (
    <div className="space-y-2 px-6 py-3 border-b">
      <Label>Torrent resolution</Label>
      <p className="text-xs text-muted-foreground">
        Applies to all files in this torrent. Corrections follow the usual review process.
      </p>
      <div className="flex gap-2">
        <Select value={resolution} onValueChange={setResolution} disabled={suggestion.isPending}>
          <SelectTrigger aria-label="Torrent resolution" className="w-40">
            <SelectValue placeholder="Not set" />
          </SelectTrigger>
          <SelectContent>
            {RESOLUTION_OPTIONS.map((option) => (
              <SelectItem key={option.value} value={option.value}>
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Button
          variant="outline"
          disabled={!resolution || resolution === submitted || suggestion.isPending}
          onClick={() => void save()}
        >
          {suggestion.isPending ? 'Saving...' : 'Save resolution'}
        </Button>
      </div>
      {message && (
        <p role="status" className="text-xs text-muted-foreground">
          {message}
        </p>
      )}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}
