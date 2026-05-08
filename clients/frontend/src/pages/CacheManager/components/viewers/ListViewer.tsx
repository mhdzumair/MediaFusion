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

interface ListViewerProps {
  data: string[]
  type: 'list' | 'set'
  onDeleteItem?: (params: { value?: string; index?: number; member?: string }) => Promise<void>
  className?: string
}

export function ListViewer({ data, type, onDeleteItem, className }: ListViewerProps) {
  const [searchTerm, setSearchTerm] = useState('')
  const [expandedRows, setExpandedRows] = useState<Set<number>>(new Set())
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null)
  const [deletingIndex, setDeletingIndex] = useState<number | null>(null)

  const filteredData = useMemo(() => {
    if (!searchTerm) return data.map((item, index) => ({ item, index }))

    const lowerSearch = searchTerm.toLowerCase()
    return data.map((item, index) => ({ item, index })).filter(({ item }) => item.toLowerCase().includes(lowerSearch))
  }, [data, searchTerm])

  const toggleExpand = (index: number) => {
    setExpandedRows((prev) => {
      const newSet = new Set(prev)
      if (newSet.has(index)) {
        newSet.delete(index)
      } else {
        newSet.add(index)
      }
      return newSet
    })
  }

  const copyValue = async (index: number, value: string) => {
    await navigator.clipboard.writeText(value)
    setCopiedIndex(index)
    setTimeout(() => setCopiedIndex(null), 2000)
  }

  const handleDelete = async (item: string, index: number) => {
    if (!onDeleteItem) return
    setDeletingIndex(index)
    try {
      if (type === 'set') {
        // For sets, use member
        await onDeleteItem({ member: item })
      } else {
        // For lists, use index
        await onDeleteItem({ index })
      }
    } finally {
      setDeletingIndex(null)
    }
  }

  const isLongValue = (value: string) => value.length > 100 || value.includes('\n')

  const formatValue = (value: string) => {
    try {
      const parsed = JSON.parse(value)
      return JSON.stringify(parsed, null, 2)
    } catch {
      return value
    }
  }

  return (
    <div className={cn('space-y-4', className)}>
      {/* Search and Stats */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search items..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-10"
          />
        </div>
        <Badge variant="secondary" className="text-sm px-3 py-1.5">
          {filteredData.length} / {data.length} items
        </Badge>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-border/50 overflow-hidden bg-card/50">
        <ScrollArea className="h-[400px]">
          <Table>
            <TableHeader className="sticky top-0 bg-muted/80 backdrop-blur-sm z-10">
              <TableRow className="hover:bg-transparent">
                <TableHead className="font-semibold w-[80px]">{type === 'list' ? 'Index' : '#'}</TableHead>
                <TableHead className="font-semibold">Value</TableHead>
                <TableHead className="w-[80px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredData.length > 0 ? (
                filteredData.map(({ item, index }) => {
                  const isExpanded = expandedRows.has(index)
                  const needsExpand = isLongValue(item)
                  const displayValue = formatValue(item)
                  const isCurrentlyDeleting = deletingIndex === index

                  return (
                    <Collapsible key={index} open={isExpanded} onOpenChange={() => needsExpand && toggleExpand(index)}>
                      <TableRow className="hover:bg-muted/30 group">
                        <TableCell className="font-mono text-sm py-3 align-top">
                          <Badge variant="outline" className="text-xs">
                            {index}
                          </Badge>
                        </TableCell>
                        <TableCell className="font-mono text-sm py-3 align-top">
                          {needsExpand ? (
                            <CollapsibleTrigger asChild>
                              <div className="cursor-pointer">
                                {isExpanded ? (
                                  <ScrollArea className="h-[300px] rounded-lg bg-muted/50 p-3">
                                    <pre className="whitespace-pre-wrap break-all text-xs">{displayValue}</pre>
                                  </ScrollArea>
                                ) : (
                                  <div className="flex items-center gap-2">
                                    <span className="truncate max-w-[400px] text-muted-foreground">
                                      {item.slice(0, 100)}...
                                    </span>
                                    <Badge variant="outline" className="text-[10px]">
                                      {item.length} chars
                                    </Badge>
                                  </div>
                                )}
                              </div>
                            </CollapsibleTrigger>
                          ) : (
                            <span className="break-all leading-relaxed">{item}</span>
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
                                    onClick={() => copyValue(index, item)}
                                  >
                                    {copiedIndex === index ? (
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
                                    <TooltipContent>Delete item</TooltipContent>
                                  </Tooltip>
                                </TooltipProvider>
                                <AlertDialogContent>
                                  <AlertDialogHeader>
                                    <AlertDialogTitle>Delete Item</AlertDialogTitle>
                                    <AlertDialogDescription>
                                      Remove this item from the {type}?
                                      <ScrollArea className="mt-2 h-[100px] rounded bg-muted p-2">
                                        <code className="block text-xs break-all">
                                          {item.length > 200 ? item.slice(0, 200) + '...' : item}
                                        </code>
                                      </ScrollArea>
                                    </AlertDialogDescription>
                                  </AlertDialogHeader>
                                  <AlertDialogFooter>
                                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                                    <AlertDialogAction
                                      onClick={() => handleDelete(item, index)}
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
                    {searchTerm ? 'No matching items found' : `No items in ${type}`}
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
