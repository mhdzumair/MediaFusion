import { useState } from 'react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Textarea } from '@/components/ui/textarea'
import { Badge } from '@/components/ui/badge'
import { Check, X, TestTube2, AlertCircle, Copy } from 'lucide-react'

interface RegexTesterModalProps {
  open: boolean
  onClose: () => void
  sourceContent: string
  fieldName: string
  currentPattern: string
  onApply: (pattern: string) => void
}

export function RegexTesterModal({
  open,
  onClose,
  sourceContent,
  fieldName,
  currentPattern,
  onApply,
}: RegexTesterModalProps) {
  const [pattern, setPattern] = useState(currentPattern)
  const [testInput, setTestInput] = useState('')
  const [matches, setMatches] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [groupNumber, setGroupNumber] = useState(1)

  // Sync pattern when currentPattern or open changes (during render, not in effect)
  const [prevOpen, setPrevOpen] = useState(open)
  const [prevPattern, setPrevPattern] = useState(currentPattern)
  if ((open && !prevOpen) || prevPattern !== currentPattern) {
    setPrevOpen(open)
    setPrevPattern(currentPattern)
    setPattern(currentPattern)
  }

  // Extract test input from source content (during render, not in effect)
  const [prevSourceContent, setPrevSourceContent] = useState(sourceContent)
  const [prevOpenForInput, setPrevOpenForInput] = useState(open)
  if ((sourceContent && prevSourceContent !== sourceContent) || (open && !prevOpenForInput)) {
    setPrevSourceContent(sourceContent)
    setPrevOpenForInput(open)
    if (sourceContent) {
      try {
        const parsed = JSON.parse(sourceContent)
        const contentFields = ['description', 'summary', 'content', 'title']
        for (const field of contentFields) {
          if (parsed[field] && typeof parsed[field] === 'string') {
            setTestInput(parsed[field])
            break
          }
        }
      } catch {
        setTestInput(sourceContent)
      }
    }
  }

  const handleTest = () => {
    setMatches([])
    setError(null)

    if (!pattern || !testInput) {
      return
    }

    try {
      const regex = new RegExp(pattern, 'gim')
      const allMatches: string[] = []
      let match

      while ((match = regex.exec(testInput)) !== null) {
        // Get the specified group or the full match
        if (match[groupNumber] !== undefined) {
          allMatches.push(match[groupNumber])
        } else if (match[0]) {
          allMatches.push(match[0])
        }

        // Prevent infinite loop for patterns that can match empty strings
        if (match.index === regex.lastIndex) {
          regex.lastIndex++
        }
      }

      setMatches(allMatches)

      if (allMatches.length === 0) {
        setError('No matches found')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Invalid regex pattern')
    }
  }

  const handleApply = () => {
    onApply(pattern)
  }

  const copyToClipboard = (text: string) => {
    navigator.clipboard.writeText(text)
  }

  // Common regex patterns
  const commonPatterns = [
    { name: 'Magnet Hash', pattern: 'magnet:\\?xt=urn:btih:([a-zA-Z0-9]+)' },
    { name: 'File Size', pattern: '([\\d.]+\\s*(?:GB|MB|KB|TB))' },
    { name: 'Seeders', pattern: 'Seeders?:\\s*(\\d+)' },
    { name: 'Info Hash', pattern: 'btih:([a-fA-F0-9]{40})' },
    { name: 'Episode', pattern: 'S(\\d{2})E(\\d{2})' },
    { name: 'Year', pattern: '\\((19|20\\d{2})\\)' },
  ]

  return (
    <Dialog open={open} onOpenChange={onClose}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <TestTube2 className="h-5 w-5 text-primary" />
            Regex Pattern Tester
          </DialogTitle>
          <DialogDescription>Test and refine your regex pattern for extracting {fieldName}</DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          {/* Common patterns */}
          <div className="space-y-2">
            <Label className="text-sm">Quick Patterns</Label>
            <div className="flex flex-wrap gap-2">
              {commonPatterns.map((p) => (
                <Badge
                  key={p.name}
                  variant="outline"
                  className="cursor-pointer hover:bg-muted"
                  onClick={() => setPattern(p.pattern)}
                >
                  {p.name}
                </Badge>
              ))}
            </div>
          </div>

          {/* Pattern input */}
          <div className="space-y-2">
            <Label htmlFor="pattern">Regex Pattern</Label>
            <div className="flex gap-2">
              <Input
                id="pattern"
                value={pattern}
                onChange={(e) => setPattern(e.target.value)}
                placeholder="Enter your regex pattern"
                className="font-mono"
              />
              <div className="flex items-center gap-1">
                <Label className="text-xs whitespace-nowrap">Group:</Label>
                <Input
                  type="number"
                  min={0}
                  max={10}
                  value={groupNumber}
                  onChange={(e) => setGroupNumber(parseInt(e.target.value) || 1)}
                  className="w-16"
                />
              </div>
            </div>
          </div>

          {/* Test input */}
          <div className="space-y-2">
            <Label htmlFor="testInput">Test Content</Label>
            <Textarea
              id="testInput"
              value={testInput}
              onChange={(e) => setTestInput(e.target.value)}
              placeholder="Paste content to test against"
              className="font-mono text-xs h-32"
            />
          </div>

          {/* Test button */}
          <Button onClick={handleTest} variant="secondary" className="w-full">
            <TestTube2 className="mr-2 h-4 w-4" />
            Test Pattern
          </Button>

          {/* Results */}
          {(matches.length > 0 || error) && (
            <div className="space-y-2">
              <Label className="text-sm">Results</Label>

              {error ? (
                <div className="flex items-center gap-2 p-3 bg-red-500/10 text-red-500 rounded-lg">
                  <AlertCircle className="h-4 w-4" />
                  <span className="text-sm">{error}</span>
                </div>
              ) : (
                <div className="space-y-2">
                  <div className="flex items-center gap-2 text-sm text-emerald-500">
                    <Check className="h-4 w-4" />
                    Found {matches.length} match{matches.length !== 1 ? 'es' : ''}
                  </div>

                  <ScrollArea className="h-40">
                    <div className="space-y-1">
                      {matches.map((match, idx) => (
                        <div
                          key={idx}
                          className="flex items-center justify-between p-2 bg-muted rounded text-xs font-mono group"
                        >
                          <span className="truncate">{match}</span>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="opacity-0 group-hover:opacity-100 h-6 w-6 p-0"
                            onClick={() => copyToClipboard(match)}
                          >
                            <Copy className="h-3 w-3" />
                          </Button>
                        </div>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              )}
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            <X className="mr-2 h-4 w-4" />
            Cancel
          </Button>
          <Button
            onClick={handleApply}
            disabled={!pattern || error !== null}
            className="bg-gradient-to-r from-primary to-primary/80"
          >
            <Check className="mr-2 h-4 w-4" />
            Apply Pattern
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
