import { useState } from 'react'
import { Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useUpdateStreamIssueTriage } from '@/hooks'
import type { IssueTriageStatus, StreamSuggestion } from '@/lib/api'

interface IssueTriageControlsProps {
  suggestion: StreamSuggestion
  onUpdated: () => void
}

export function IssueTriageControls({ suggestion, onUpdated }: IssueTriageControlsProps) {
  const updateTriage = useUpdateStreamIssueTriage()
  const [status, setStatus] = useState<IssueTriageStatus>(
    (suggestion.issue_triage_status as IssueTriageStatus) || 'open',
  )
  const [note, setNote] = useState(suggestion.issue_triage_note || '')

  const handleSave = async () => {
    await updateTriage.mutateAsync({
      suggestionId: suggestion.id,
      data: { issue_triage_status: status, issue_triage_note: note.trim() || undefined },
    })
    onUpdated()
  }

  return (
    <div className="mt-2 space-y-2 rounded-lg border border-border/50 bg-muted/20 p-2">
      <p className="text-[10px] font-medium uppercase tracking-wide text-muted-foreground">Issue triage</p>
      <div className="flex flex-wrap items-center gap-2">
        <Select value={status} onValueChange={(v) => setStatus(v as IssueTriageStatus)}>
          <SelectTrigger className="h-8 w-[160px] text-xs">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="open">Open</SelectItem>
            <SelectItem value="reviewed">Reviewed</SelectItem>
            <SelectItem value="dismissed">Dismissed</SelectItem>
            <SelectItem value="action_taken">Action taken</SelectItem>
          </SelectContent>
        </Select>
        <Button
          type="button"
          size="sm"
          className="h-8 rounded-lg"
          disabled={updateTriage.isPending}
          onClick={() => void handleSave()}
        >
          {updateTriage.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : 'Save triage'}
        </Button>
      </div>
      <Textarea
        value={note}
        onChange={(e) => setNote(e.target.value)}
        placeholder="Optional triage note (moderator-only)"
        rows={2}
        className="text-xs min-h-0"
      />
    </div>
  )
}
