import { useState, useMemo } from 'react'
import { Search, Copy, Check, ArrowUpDown, Trash2, Loader2 } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import { formatTimestamp } from '../../types'

interface ZSetEntry {
  member: string
  score: number
}

interface ZSetViewerProps {
  data: ZSetEntry[]
  onDeleteItem?: (member: string) => Promise<void>
  className?: string
}

type SortField = 'score' | 'member'
type SortOrder = 'asc' | 'desc'

export function ZSetViewer({ data, onDeleteItem, className }: ZSetViewerProps) {
  const [searchTerm, setSearchTerm] = useState('')
  const [sortField, setSortField] = useState<SortField>('score')
  const [sortOrder, setSortOrder] = useState<SortOrder>('desc')
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null)
  const [deletingMember, setDeletingMember] = useState<string | null>(null)
  
  const sortedAndFilteredData = useMemo(() => {
    let result = [...data]
    
    // Filter
    if (searchTerm) {
      const lowerSearch = searchTerm.toLowerCase()
      result = result.filter(entry => 
        entry.member.toLowerCase().includes(lowerSearch) ||
        String(entry.score).includes(searchTerm)
      )
    }
    
    // Sort
    result.sort((a, b) => {
      const multiplier = sortOrder === 'asc' ? 1 : -1
      if (sortField === 'score') {
        return (a.score - b.score) * multiplier
      }
      return a.member.localeCompare(b.member) * multiplier
    })
    
    return result
  }, [data, searchTerm, sortField, sortOrder])
  
  const toggleSort = (field: SortField) => {
    if (sortField === field) {
      setSortOrder(prev => prev === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortOrder('desc')
    }
  }
  
  const copyValue = async (index: number, value: string) => {
    await navigator.clipboard.writeText(value)
    setCopiedIndex(index)
    setTimeout(() => setCopiedIndex(null), 2000)
  }
  
  const handleDelete = async (member: string) => {
    if (!onDeleteItem) return
    setDeletingMember(member)
    try {
      await onDeleteItem(member)
    } finally {
      setDeletingMember(null)
    }
  }
  
  return (
    <div className={cn("space-y-4", className)}>
      {/* Search and Stats */}
      <div className="flex items-center gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
          <Input
            placeholder="Search members or scores..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9 h-10"
          />
        </div>
        <Badge variant="secondary" className="text-sm px-3 py-1.5">
          {sortedAndFilteredData.length} / {data.length} entries
        </Badge>
      </div>
      
      {/* Table */}
      <div className="rounded-xl border border-border/50 overflow-hidden bg-card/50">
        <ScrollArea className="h-[400px]">
          <Table>
            <TableHeader className="sticky top-0 bg-muted/80 backdrop-blur-sm z-10">
              <TableRow className="hover:bg-transparent">
                <TableHead className="w-[180px]">
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    className="h-auto p-0 font-semibold hover:bg-transparent gap-2"
                    onClick={() => toggleSort('score')}
                  >
                    Score
                    <ArrowUpDown className={cn(
                      "h-3.5 w-3.5",
                      sortField === 'score' && "text-primary"
                    )} />
                  </Button>
                </TableHead>
                <TableHead>
                  <Button 
                    variant="ghost" 
                    size="sm" 
                    className="h-auto p-0 font-semibold hover:bg-transparent gap-2"
                    onClick={() => toggleSort('member')}
                  >
                    Member
                    <ArrowUpDown className={cn(
                      "h-3.5 w-3.5",
                      sortField === 'member' && "text-primary"
                    )} />
                  </Button>
                </TableHead>
                <TableHead className="w-[80px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {sortedAndFilteredData.length > 0 ? sortedAndFilteredData.map((entry, index) => {
                const scoreFormatted = formatTimestamp(entry.score)
                const isCurrentlyDeleting = deletingMember === entry.member
                
                return (
                  <TableRow key={index} className="hover:bg-muted/30 group">
                    <TableCell className="font-mono text-sm py-3">
                      {scoreFormatted.isTimestamp ? (
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="text-emerald-400 cursor-help">
                                {scoreFormatted.display}
                              </span>
                            </TooltipTrigger>
                            <TooltipContent>
                              <p>Unix: {entry.score}</p>
                            </TooltipContent>
                          </Tooltip>
                        </TooltipProvider>
                      ) : (
                        <span className="text-primary">
                          {entry.score.toLocaleString()}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-sm py-3">
                      <span className="text-primary break-all">{entry.member}</span>
                    </TableCell>
                    <TableCell className="py-3 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <TooltipProvider>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button 
                                variant="ghost" 
                                size="icon" 
                                className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity"
                                onClick={() => copyValue(index, entry.member)}
                              >
                                {copiedIndex === index ? (
                                  <Check className="h-3.5 w-3.5 text-emerald-400" />
                                ) : (
                                  <Copy className="h-3.5 w-3.5" />
                                )}
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Copy member</TooltipContent>
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
                                <TooltipContent>Delete member</TooltipContent>
                              </Tooltip>
                            </TooltipProvider>
                            <AlertDialogContent>
                              <AlertDialogHeader>
                                <AlertDialogTitle>Delete Member</AlertDialogTitle>
                                <AlertDialogDescription>
                                  Remove this member from the sorted set?
                                  <code className="block mt-2 p-2 bg-muted rounded text-xs break-all">
                                    {entry.member}
                                  </code>
                                </AlertDialogDescription>
                              </AlertDialogHeader>
                              <AlertDialogFooter>
                                <AlertDialogCancel>Cancel</AlertDialogCancel>
                                <AlertDialogAction
                                  onClick={() => handleDelete(entry.member)}
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
                )
              }) : (
                <TableRow>
                  <TableCell colSpan={3} className="text-center py-12 text-muted-foreground">
                    {searchTerm ? 'No matching entries found' : 'No entries in sorted set'}
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
