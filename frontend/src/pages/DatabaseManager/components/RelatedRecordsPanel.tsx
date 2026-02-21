import { ArrowRight, ArrowLeft, ExternalLink, Loader2, Network } from 'lucide-react'
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetDescription } from '@/components/ui/sheet'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'
import type { RelatedRecordsResponse } from '../types'

interface RelatedRecordsPanelProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  tableName: string
  rowId: string
  idColumn: string
  data: RelatedRecordsResponse | null
  isLoading: boolean
  onNavigate: (table: string, column: string, value: string) => void
}

function TruncatedMono({ text, className }: { text: string; className?: string }) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className={cn('font-mono truncate block', className)}>{text}</span>
        </TooltipTrigger>
        <TooltipContent side="top" className="font-mono text-xs max-w-sm break-all">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

function PreviewCard({ preview }: { preview: Record<string, unknown> }) {
  const entries = Object.entries(preview).slice(0, 5)
  return (
    <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      {entries.map(([key, value]) => {
        const displayValue = value === null ? 'NULL' : typeof value === 'object' ? JSON.stringify(value) : String(value)
        return (
          <div key={key} className="contents">
            <span className="font-mono text-muted-foreground shrink-0">{key}</span>
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className={cn('font-mono truncate', value === null && 'text-muted-foreground italic')}>
                    {displayValue}
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="font-mono text-xs max-w-sm break-all">
                  {displayValue}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          </div>
        )
      })}
    </div>
  )
}

export function RelatedRecordsPanel({
  open,
  onOpenChange,
  tableName,
  rowId,
  idColumn,
  data,
  isLoading,
  onNavigate,
}: RelatedRecordsPanelProps) {
  const refs = data?.references ?? []
  const outgoing = refs.filter((r) => r.direction === 'outgoing')
  const incoming = refs.filter((r) => r.direction === 'incoming')

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-[520px] sm:w-[600px] flex flex-col overflow-hidden">
        <SheetHeader className="shrink-0">
          <SheetTitle className="flex items-center gap-2">
            <Network className="h-4 w-4" />
            Related Records
          </SheetTitle>
          <SheetDescription className="font-mono text-xs truncate">
            {tableName} where {idColumn} = {rowId}
          </SheetDescription>
        </SheetHeader>

        <ScrollArea className="mt-4 min-h-0 flex-1 pr-1">
          <div className="space-y-6">
            {isLoading ? (
              <div className="flex items-center justify-center py-12">
                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
              </div>
            ) : (
              <>
                {/* Outgoing References */}
                <div>
                  <div className="flex items-center gap-2 mb-3">
                    <ArrowRight className="h-4 w-4 text-blue-400 shrink-0" />
                    <h4 className="text-sm font-medium">Outgoing References</h4>
                    <Badge variant="secondary" className="text-xs">
                      {outgoing.length}
                    </Badge>
                  </div>

                  {outgoing.length === 0 ? (
                    <p className="text-xs text-muted-foreground pl-6">No outgoing foreign keys</p>
                  ) : (
                    <div className="space-y-2 pl-6">
                      {outgoing.map((ref, i) => (
                        <div key={i} className="rounded-lg border border-border/50 bg-muted/20 p-3 space-y-2">
                          {/* FK path — full width with tooltip */}
                          <TruncatedMono
                            text={`${ref.column} → ${ref.referenced_table}.${ref.referenced_column}`}
                            className="text-xs text-muted-foreground"
                          />

                          {/* Preview or empty state */}
                          {ref.preview ? (
                            <PreviewCard preview={ref.preview} />
                          ) : (
                            <p className="text-xs text-muted-foreground italic">No referenced row found</p>
                          )}

                          {/* Action */}
                          <div className="flex justify-end">
                            <Button
                              variant="outline"
                              size="sm"
                              className="h-6 px-2 text-xs gap-1"
                              onClick={() => onNavigate(ref.referenced_table, ref.referenced_column, rowId)}
                              disabled={ref.row_count === 0}
                            >
                              Go to
                              <ExternalLink className="h-3 w-3" />
                            </Button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <Separator />

                {/* Incoming References */}
                <div>
                  <div className="flex items-center gap-2 mb-3">
                    <ArrowLeft className="h-4 w-4 text-emerald-400 shrink-0" />
                    <h4 className="text-sm font-medium">Incoming References</h4>
                    <Badge variant="secondary" className="text-xs">
                      {incoming.length}
                    </Badge>
                  </div>

                  {incoming.length === 0 ? (
                    <p className="text-xs text-muted-foreground pl-6">No other tables reference this row</p>
                  ) : (
                    <div className="space-y-2 pl-6">
                      {incoming.map((ref, i) => (
                        <div key={i} className="rounded-lg border border-border/50 bg-muted/20 p-3 space-y-2">
                          {/* Table.column with tooltip */}
                          <TruncatedMono text={`${ref.table}.${ref.column}`} className="text-xs text-foreground" />
                          {/* Badge + action */}
                          <div className="flex items-center justify-between gap-2">
                            <Badge
                              variant="outline"
                              className={cn(
                                'text-xs',
                                ref.row_count > 0
                                  ? 'text-emerald-400 border-emerald-500/30'
                                  : 'text-muted-foreground border-border',
                              )}
                            >
                              {ref.row_count === -1 ? '?' : ref.row_count} row{ref.row_count !== 1 ? 's' : ''}
                            </Badge>
                            {ref.row_count > 0 && (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-6 px-2 text-xs gap-1 shrink-0"
                                onClick={() => onNavigate(ref.table, ref.column, rowId)}
                              >
                                View
                                <ExternalLink className="h-3 w-3" />
                              </Button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
