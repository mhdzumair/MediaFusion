import { useState, useMemo } from 'react'
import { Search, Copy, Check, ChevronDown, ChevronUp, Trash2, Loader2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Collapsible, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { formatTimestamp } from '../../types'

interface HashViewerProps {
  data: Record<string, string | number>
  onDeleteItem?: (field: string) => Promise<void>
  className?: string
}

export function HashViewer({ data, onDeleteItem, className }: HashViewerProps) {
  const [searchTerm, setSearchTerm] = useState('')
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set())
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [deletingField, setDeletingField] = useState<string | null>(null)

  const entries = useMemo(() => {
    const allEntries = Object.entries(data)
    if (!searchTerm) return allEntries

    const lowerSearch = searchTerm.toLowerCase()
    return allEntries.filter(
      ([key, value]) => key.toLowerCase().includes(lowerSearch) || String(value).toLowerCase().includes(lowerSearch),
    )
  }, [data, searchTerm])

  const toggleExpand = (key: string) => {
    setExpandedRows((prev) => {
      const newSet = new Set(prev)
      if (newSet.has(key)) {
        newSet.delete(key)
      } else {
        newSet.add(key)
      }
      return newSet
    })
  }

  const copyValue = async (key: string, value: string) => {
    await navigator.clipboard.writeText(value)
    setCopiedKey(key)
    setTimeout(() => setCopiedKey(null), 2000)
  }

  const handleDelete = async (field: string) => {
    if (!onDeleteItem) return
    setDeletingField(field)
    try {
      await onDeleteItem(field)
    } finally {
      setDeletingField(null)
    }
  }

  const isLongValue = (value: string) => value.length > 100 || value.includes('\n')

  const formatValue = (value: string | number) => {
    const strValue = String(value)
    try {
      const parsed = JSON.parse(strValue)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return strValue
    }
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Search and Stats */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search fields or values..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-10"
          />
        </div>
        <Badge variant="secondary" className="text-sm px-3 py-1.5">
          {entries.length} / {Object.keys(data).length} entries
        </Badge>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border/50 overflow-hidden bg-card/50">
        <ScrollArea className="h-[400px]">
          <Table>
            <TableHeader className="sticky top-0 bg-muted/80 backdrop-blur-sm z-10">
              <TableRow className="hover:bg-transparent">
                <TableHead className="font-semibold w-[40%]">Field</TableHead>
                <TableHead className="font-semibold">Value</TableHead>
                <TableHead className="w-[100px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entries.length > 0 ? (
                entries.map(([key, value]) => {
                  const strValue = String(value)
                  const formatted = formatTimestamp(value)
                  const isExpanded = expandedRows.has(key)
                  const needsExpand = isLongValue(strValue)
                  const displayValue = formatValue(value)
                  const isCurrentlyDeleting = deletingField === key

                  return (
                    <Collapsible key={key} open={isExpanded} onOpenChange={() => needsExpand && toggleExpand(key)}>
                      <TableRow className="hover:bg-muted/30 group">
                        <TableCell className="font-mono text-sm py-3 align-top">
                          <span className="text-primary break-all leading-relaxed">{key}</span>
                        </TableCell>
                        <TableCell className="font-mono text-sm py-3 align-top">
                          {formatted.isTimestamp ? (
                            <div className="flex flex-col gap-1">
                              <span className="text-emerald-400">{formatted.display}</span>
                              <span className="text-xs text-muted-foreground">(Unix: {value})</span>
                            </div>
                          ) : needsExpand ? (
                            <CollapsibleTrigger asChild>
                              <div className="cursor-pointer">
                                {isExpanded ? (
                                  <pre className="whitespace-pre-wrap break-all text-xs bg-muted/50 p-3 rounded-lg max-h-[300px] overflow-auto">
                                    {displayValue}
                                  </pre>
                                ) : (
                                  <div className="flex items-center gap-2">
                                    <span className="truncate max-w-[300px] text-muted-foreground">
                                      {strValue.slice(0, 80)}...
                                    </span>
                                    <Badge variant="outline" className="text-[10px]">
                                      {strValue.length} chars
                                    </Badge>
                                  </div>
                                )}
                              </div>
                            </CollapsibleTrigger>
                          ) : (
                            <span className="break-all leading-relaxed">{strValue}</span>
                          )}
                        </TableCell>
                        <TableCell className="py-3 align-top text-right">
                          <div className="flex items-center justify-end gap-1">
                            {needsExpand && (
                              <CollapsibleTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity"
                                >
                                  {isExpanded ? (
                                    <ChevronUp className="h-3.5 w-3.5" />
                                  ) : (
                                    <ChevronDown className="h-3.5 w-3.5" />
                                  )}
                                </Button>
                              </CollapsibleTrigger>
                            )}

                            <TooltipProvider>
                              <Tooltip>
                                <TooltipTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="icon"
                                    className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity"
                                    onClick={() => copyValue(key, strValue)}
                                  >
                                    {copiedKey === key ? (
                                      <Check className="h-3.5 w-3.5 text-emerald-400" />
                                    ) : (
                                      <Copy className="h-3.5 w-3.5" />
                                    )}
                                  </Button>
                                </TooltipTrigger>
                                <TooltipContent>Copy value</TooltipContent>
                              </Tooltip>
                            </TooltipProvider>

                            {onDeleteItem && (
                              <AlertDialog>
                                <TooltipProvider>
                                  <Tooltip>
                                    <TooltipTrigger asChild>
                                      <AlertDialogTrigger asChild>
                                        <Button
                                          variant="ghost"
                                          size="icon"
                                          className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity text-destructive hover:text-destructive"
                                          disabled={isCurrentlyDeleting}
                                        >
                                          {isCurrentlyDeleting ? (
                                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                          ) : (
                                            <Trash2 className="h-3.5 w-3.5" />
                                          )}
                                        </Button>
                                      </AlertDialogTrigger>
                                    </TooltipTrigger>
                                    <TooltipContent>Delete field</TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                                <AlertDialogContent>
                                  <AlertDialogHeader>
                                    <AlertDialogTitle>Delete Field</AlertDialogTitle>
                                    <AlertDialogDescription>
                                      Remove this field from the hash?
                                      <code className="block mt-2 p-2 bg-muted rounded text-xs break-all">{key}</code>
                                    </AlertDialogDescription>
                                  </AlertDialogHeader>
                                  <AlertDialogFooter>
                                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                                    <AlertDialogAction
                                      onClick={() => handleDelete(key)}
                                      className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                                    >
                                      Delete
                                    </AlertDialogAction>
                                  </AlertDialogFooter>
                                </AlertDialogContent>
                              </AlertDialog>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    </Collapsible>
                  )
                })
              ) : (
                <TableRow>
                  <TableCell colSpan={3} className="text-center py-12 text-muted-foreground">
                    {searchTerm ? 'No matching entries found' : 'No entries in hash'}
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </ScrollArea>
      </div>
    </div>
  )
}
