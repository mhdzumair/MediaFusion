import { useState, useMemo, useCallback } from 'react'
import {
  Search,
  Table2,
  Key,
  Link2,
  Hash,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
  Download,
  Upload,
  Trash2,
  RefreshCw,
  SortAsc,
  SortDesc,
  FileJson,
  FileText,
  Database,
  ArrowUpDown,
  Check,
  X,
  Eye,
  Pencil,
  Filter,
  Wand2,
  XCircle,
  Menu,
  MoreVertical,
  ExternalLink,
  Network,
} from 'lucide-react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'
import {
  useTableList,
  useTableSchema,
  useTableData,
  useExportTable,
  useBulkDelete,
  useBulkUpdate,
  useImportData,
  useRelatedRecords,
} from '../hooks/useDatabaseData'
import { formatTimestamp, getTableTypeColor, truncateText } from '../types'
import type { ColumnInfo } from '../types'
import { EditRowDialog } from './EditRowDialog'
import { RelatedRecordsPanel } from './RelatedRecordsPanel'

interface TableBrowserTabProps {
  initialTable?: string
}

interface FilterState {
  id: string
  column: string
  operator: string
  value: string
}

interface NavigationEntry {
  table: string
  filters: FilterState[]
  label: string
}

// Column type badge
function ColumnTypeBadge({ dataType }: { dataType: string }) {
  const getTypeColor = (type: string) => {
    const lowerType = type.toLowerCase()
    if (lowerType.includes('int') || lowerType.includes('numeric') || lowerType.includes('float')) {
      return 'bg-blue-500/20 text-blue-400 border-blue-500/30'
    }
    if (lowerType.includes('text') || lowerType.includes('varchar') || lowerType.includes('char')) {
      return 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30'
    }
    if (lowerType.includes('bool')) {
      return 'bg-primary/20 text-primary border-primary/30'
    }
    if (lowerType.includes('timestamp') || lowerType.includes('date') || lowerType.includes('time')) {
      return 'bg-primary/20 text-primary border-primary/30'
    }
    if (lowerType.includes('json') || lowerType.includes('array')) {
      return 'bg-rose-500/20 text-rose-400 border-rose-500/30'
    }
    if (lowerType.includes('uuid')) {
      return 'bg-cyan-500/20 text-cyan-400 border-cyan-500/30'
    }
    return 'bg-slate-500/20 text-slate-400 border-slate-500/30'
  }

  return (
    <Badge variant="outline" className={cn('text-xs font-mono shrink-0', getTypeColor(dataType))}>
      {dataType}
    </Badge>
  )
}

// Schema viewer component with collapsible sections
function SchemaViewer({
  schema,
  onNavigateTable,
}: {
  schema: ReturnType<typeof useTableSchema>['data']
  onNavigateTable?: (table: string) => void
}) {
  const [columnsOpen, setColumnsOpen] = useState(true)
  const [indexesOpen, setIndexesOpen] = useState(false)
  const [fkOpen, setFkOpen] = useState(false)

  if (!schema) return null

  return (
    <div className="space-y-2">
      {/* Columns - Collapsible */}
      <Collapsible open={columnsOpen} onOpenChange={setColumnsOpen}>
        <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 rounded-lg hover:bg-muted/50 transition-colors">
          {columnsOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
          <Table2 className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm font-medium">Columns</span>
          <Badge variant="secondary" className="ml-auto text-xs">
            {schema.columns.length}
          </Badge>
        </CollapsibleTrigger>
        <CollapsibleContent className="pl-2">
          <div className="space-y-0.5 mt-1">
            {schema.columns.map((col) => (
              <div key={col.name} className="flex items-center gap-2 p-2 rounded-lg hover:bg-muted/50 text-sm group">
                <div className="flex items-center gap-1.5 min-w-0 flex-1">
                  {col.is_primary_key && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger>
                          <Key className="h-3.5 w-3.5 text-primary shrink-0" />
                        </TooltipTrigger>
                        <TooltipContent>Primary Key</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}
                  {col.is_foreign_key && (
                    <TooltipProvider>
                      <Tooltip>
                        <TooltipTrigger>
                          <Link2 className="h-3.5 w-3.5 text-blue-400 shrink-0" />
                        </TooltipTrigger>
                        <TooltipContent>Foreign Key → {col.foreign_key_ref}</TooltipContent>
                      </Tooltip>
                    </TooltipProvider>
                  )}
                  <span className="font-mono text-xs truncate">{col.name}</span>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <ColumnTypeBadge dataType={col.data_type} />
                  {!col.is_nullable && (
                    <Badge variant="outline" className="text-[10px] px-1 text-rose-400 border-rose-500/30">
                      NN
                    </Badge>
                  )}
                  {col.is_foreign_key && col.foreign_key_ref && onNavigateTable && (
                    <button
                      onClick={() => onNavigateTable(col.foreign_key_ref!.split('.')[0])}
                      className="text-[10px] px-1.5 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/30 hover:bg-blue-500/20 transition-colors font-mono opacity-0 group-hover:opacity-100"
                      title={`Navigate to ${col.foreign_key_ref!.split('.')[0]}`}
                    >
                      →
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </CollapsibleContent>
      </Collapsible>

      {/* Indexes - Collapsible */}
      {schema.indexes.length > 0 && (
        <Collapsible open={indexesOpen} onOpenChange={setIndexesOpen}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 rounded-lg hover:bg-muted/50 transition-colors">
            {indexesOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            <Hash className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">Indexes</span>
            <Badge variant="secondary" className="ml-auto text-xs">
              {schema.indexes.length}
            </Badge>
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-2">
            <div className="space-y-0.5 mt-1">
              {schema.indexes.map((idx) => (
                <div key={idx.name} className="p-2 rounded-lg hover:bg-muted/50 text-sm">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs truncate flex-1">{idx.name}</span>
                    {idx.is_unique && (
                      <Badge variant="outline" className="text-[10px] px-1 text-emerald-400 border-emerald-500/30">
                        UQ
                      </Badge>
                    )}
                    {idx.is_primary && (
                      <Badge variant="outline" className="text-[10px] px-1 text-primary border-primary/30">
                        PK
                      </Badge>
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground mt-1 font-mono">({idx.columns.join(', ')})</div>
                </div>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}

      {/* Foreign Keys - Collapsible */}
      {schema.foreign_keys.length > 0 && (
        <Collapsible open={fkOpen} onOpenChange={setFkOpen}>
          <CollapsibleTrigger className="flex items-center gap-2 w-full p-2 rounded-lg hover:bg-muted/50 transition-colors">
            {fkOpen ? <ChevronDown className="h-4 w-4" /> : <ChevronUp className="h-4 w-4" />}
            <Link2 className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">Foreign Keys</span>
            <Badge variant="secondary" className="ml-auto text-xs">
              {schema.foreign_keys.length}
            </Badge>
          </CollapsibleTrigger>
          <CollapsibleContent className="pl-2">
            <div className="space-y-0.5 mt-1">
              {schema.foreign_keys.map((fk) => (
                <div key={fk.name} className="p-2 rounded-lg hover:bg-muted/50 text-sm">
                  <div className="font-mono text-[10px] text-muted-foreground truncate">{fk.name}</div>
                  <div className="flex items-center gap-1.5 mt-1 text-xs">
                    <span className="font-mono">{fk.columns.join(', ')}</span>
                    <span className="text-muted-foreground">→</span>
                    {onNavigateTable ? (
                      <button
                        onClick={() => onNavigateTable(fk.referenced_table)}
                        className="font-mono text-blue-400 hover:text-blue-300 hover:underline transition-colors"
                      >
                        {fk.referenced_table}
                      </button>
                    ) : (
                      <span className="font-mono text-blue-400">{fk.referenced_table}</span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}

// Data cell renderer with expandable content and FK link support
function DataCell({
  value,
  column,
  columnInfo,
  onNavigate,
}: {
  value: unknown
  column: string
  columnInfo?: ColumnInfo
  onNavigate?: (table: string, column: string, value: string) => void
}) {
  const [expanded, setExpanded] = useState(false)

  if (value === null || value === undefined) {
    return <span className="text-muted-foreground italic text-xs">NULL</span>
  }

  if (typeof value === 'boolean') {
    return value ? (
      <Badge variant="outline" className="text-emerald-400 border-emerald-500/30 text-xs">
        <Check className="h-3 w-3 mr-1" />
        true
      </Badge>
    ) : (
      <Badge variant="outline" className="text-rose-400 border-rose-500/30 text-xs">
        <X className="h-3 w-3 mr-1" />
        false
      </Badge>
    )
  }

  if (typeof value === 'object') {
    const jsonStr = JSON.stringify(value)
    const isLong = jsonStr.length > 50

    return (
      <div className="max-w-[200px]">
        <span
          className={cn(
            'font-mono text-xs text-primary cursor-pointer hover:text-primary/80',
            !expanded && 'line-clamp-1',
          )}
          onClick={() => isLong && setExpanded(!expanded)}
        >
          {expanded ? JSON.stringify(value, null, 2) : truncateText(jsonStr, 50)}
        </span>
        {isLong && (
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-xs text-muted-foreground hover:text-foreground ml-1"
          >
            {expanded ? '(less)' : '(more)'}
          </button>
        )}
      </div>
    )
  }

  const strValue = String(value)

  // FK link rendering
  if (columnInfo?.is_foreign_key && columnInfo.foreign_key_ref && onNavigate) {
    const [targetTable, targetColumn] = columnInfo.foreign_key_ref.split('.')
    if (targetTable && targetColumn) {
      return (
        <button
          onClick={() => onNavigate(targetTable, targetColumn, strValue)}
          className="inline-flex items-center gap-1 text-blue-400 hover:text-blue-300 transition-colors text-sm font-mono group"
          title={`Go to ${targetTable} where ${targetColumn} = ${strValue}`}
        >
          <span>{strValue}</span>
          <ExternalLink className="h-3 w-3 opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
        </button>
      )
    }
  }

  // Check if it's a timestamp
  if (column.includes('_at') || column.includes('date') || column.includes('time')) {
    return <span className="text-xs whitespace-nowrap">{formatTimestamp(strValue)}</span>
  }

  // Long text with expand option
  if (strValue.length > 60) {
    return (
      <div className="max-w-[250px]">
        <span
          className={cn('text-sm cursor-pointer', !expanded && 'line-clamp-1')}
          onClick={() => setExpanded(!expanded)}
        >
          {expanded ? strValue : truncateText(strValue, 60)}
        </span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-muted-foreground hover:text-foreground ml-1"
        >
          {expanded ? '(less)' : '(more)'}
        </button>
      </div>
    )
  }

  return <span className="text-sm whitespace-nowrap">{strValue}</span>
}

export function TableBrowserTab({ initialTable }: TableBrowserTabProps) {
  const [selectedTable, setSelectedTable] = useState<string | null>(initialTable || null)
  const [tableSearch, setTableSearch] = useState('')
  const [page, setPage] = useState(1)
  const [perPage, setPerPage] = useState(25)
  const [orderBy, setOrderBy] = useState<string | undefined>()
  const [orderDir, setOrderDir] = useState<'asc' | 'desc'>('asc')
  const [selectedRows, setSelectedRows] = useState<string[]>([])
  const [showSchema, setShowSchema] = useState(true)

  // Filter state - support multiple filters
  const [filters, setFilters] = useState<FilterState[]>([])

  // Helper to generate unique filter ID
  const generateFilterId = () => `filter_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`

  // Export dialog state
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [exportFormat, setExportFormat] = useState<'csv' | 'json' | 'sql'>('csv')
  const [exportIncludeSchema, setExportIncludeSchema] = useState(true)
  const [exportIncludeData, setExportIncludeData] = useState(true)

  // Import dialog state
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [importFile, setImportFile] = useState<File | null>(null)
  const [importFormat, setImportFormat] = useState<'csv' | 'json' | 'sql'>('csv')
  const [importMode, setImportMode] = useState<'insert' | 'upsert' | 'replace'>('insert')

  // Delete dialog state
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [cascadeDelete, setCascadeDelete] = useState(false)

  // Edit dialog state
  const [editDialogOpen, setEditDialogOpen] = useState(false)
  const [editingRow, setEditingRow] = useState<Record<string, unknown> | null>(null)

  // Bulk update dialog state
  const [bulkUpdateDialogOpen, setBulkUpdateDialogOpen] = useState(false)
  const [bulkUpdateColumn, setBulkUpdateColumn] = useState('')
  const [bulkUpdateValue, setBulkUpdateValue] = useState('')

  // FK navigation state
  const [navigationStack, setNavigationStack] = useState<NavigationEntry[]>([])

  // Related records panel state
  const [relatedPanelOpen, setRelatedPanelOpen] = useState(false)
  const [relatedRow, setRelatedRow] = useState<Record<string, unknown> | null>(null)

  const { toast } = useToast()

  // Queries
  const { data: tables, isLoading: tablesLoading } = useTableList()
  const { data: schema, isLoading: schemaLoading } = useTableSchema(selectedTable)

  // Helper function to check if an operator needs a value
  const operatorNeedsValue = (operator: string) =>
    !['is_null', 'is_not_null', 'array_empty', 'array_not_empty', 'json_is_null', 'json_is_not_null'].includes(operator)

  // Build filter conditions for API
  const filterConditions = filters
    .filter((f) => f.column && (!operatorNeedsValue(f.operator) || f.value))
    .map((f) => ({
      column: f.column,
      operator: f.operator as import('@/lib/api/admin').FilterOperator,
      value: operatorNeedsValue(f.operator) ? f.value : undefined,
    }))

  const {
    data: tableData,
    isLoading: dataLoading,
    refetch: refetchData,
  } = useTableData(selectedTable, {
    page,
    per_page: perPage,
    order_by: orderBy,
    order_dir: orderDir,
    filters: filterConditions.length > 0 ? filterConditions : undefined,
  })

  // Mutations
  const exportMutation = useExportTable()
  const bulkDeleteMutation = useBulkDelete()
  const bulkUpdateMutation = useBulkUpdate()
  const importDataMutation = useImportData()

  // Filter tables by search
  const filteredTables = useMemo(() => {
    if (!tables?.tables) return []
    if (!tableSearch) return tables.tables
    return tables.tables.filter((t) => t.name.toLowerCase().includes(tableSearch.toLowerCase()))
  }, [tables, tableSearch])

  // Get ID column for selected table
  const idColumn = useMemo(() => {
    if (!schema?.columns) return 'id'
    const pkCol = schema.columns.find((c) => c.is_primary_key)
    return pkCol?.name || 'id'
  }, [schema])

  // Build a column name -> ColumnInfo lookup for FK rendering in DataCell
  const columnInfoMap = useMemo(() => {
    if (!schema?.columns) return new Map<string, ColumnInfo>()
    return new Map(schema.columns.map((c) => [c.name, c]))
  }, [schema])

  // Related records query (only active when panel is open)
  const relatedRowId = relatedRow ? String(relatedRow[idColumn]) : null
  const { data: relatedData, isLoading: relatedLoading } = useRelatedRecords(
    relatedPanelOpen ? selectedTable : null,
    relatedPanelOpen ? relatedRowId : null,
    idColumn,
  )

  // FK navigation: push current state and switch table with filter
  const handleFKNavigate = useCallback(
    (targetTable: string, targetColumn: string, value: string) => {
      if (!selectedTable) return
      setNavigationStack((prev) => [...prev, { table: selectedTable, filters, label: `${selectedTable}` }])
      setSelectedTable(targetTable)
      setFilters([
        {
          id: `fk_nav_${Date.now()}`,
          column: targetColumn,
          operator: 'equals',
          value,
        },
      ])
      setPage(1)
      setSelectedRows([])
    },
    [selectedTable, filters],
  )

  // Navigate back to a specific breadcrumb entry
  const handleBreadcrumbNavigate = useCallback(
    (index: number) => {
      const entry = navigationStack[index]
      if (!entry) return
      setSelectedTable(entry.table)
      setFilters(entry.filters)
      setPage(1)
      setSelectedRows([])
      setNavigationStack((prev) => prev.slice(0, index))
    },
    [navigationStack],
  )

  // Clear navigation history entirely
  const handleClearNavigation = useCallback(() => {
    setNavigationStack([])
    setFilters([])
    setPage(1)
  }, [])

  // Navigate to a table (from schema sidebar) without a filter
  const handleNavigateToTable = useCallback(
    (targetTable: string) => {
      if (!selectedTable) return
      setNavigationStack((prev) => [...prev, { table: selectedTable, filters, label: selectedTable }])
      setSelectedTable(targetTable)
      setFilters([])
      setPage(1)
      setSelectedRows([])
    },
    [selectedTable, filters],
  )

  // Open related records panel for a row
  const handleViewRelated = useCallback((row: Record<string, unknown>) => {
    setRelatedRow(row)
    setRelatedPanelOpen(true)
  }, [])

  // Handle sort
  const handleSort = (column: string) => {
    if (orderBy === column) {
      setOrderDir(orderDir === 'asc' ? 'desc' : 'asc')
    } else {
      setOrderBy(column)
      setOrderDir('asc')
    }
  }

  // Handle row selection
  const handleRowSelect = (id: string) => {
    setSelectedRows((prev) => (prev.includes(id) ? prev.filter((r) => r !== id) : [...prev, id]))
  }

  const handleSelectAll = () => {
    if (!tableData?.rows) return
    const allIds = tableData.rows.map((r) => String(r[idColumn]))
    if (selectedRows.length === allIds.length) {
      setSelectedRows([])
    } else {
      setSelectedRows(allIds)
    }
  }

  // Handle edit row
  const handleEditRow = (row: Record<string, unknown>) => {
    setEditingRow(row)
    setEditDialogOpen(true)
  }

  // Handle save edit
  const handleSaveEdit = async (updates: Record<string, unknown>) => {
    if (!selectedTable || !editingRow) return

    const rowId = String(editingRow[idColumn])

    const result = await bulkUpdateMutation.mutateAsync({
      table: selectedTable,
      ids: [rowId],
      id_column: idColumn,
      updates,
    })

    if (result.success) {
      toast({
        title: 'Row updated',
        description: `Successfully updated ${result.rows_affected} row(s)`,
      })
      refetchData()
    }
  }

  // Handle export
  const handleExport = async () => {
    if (!selectedTable) return

    try {
      await exportMutation.mutateAsync({
        tableName: selectedTable,
        format: exportFormat,
        options: {
          include_schema: exportIncludeSchema,
          include_data: exportIncludeData,
        },
      })
      toast({
        title: 'Export complete',
        description: `${selectedTable} exported as ${exportFormat.toUpperCase()}`,
      })
      setExportDialogOpen(false)
    } catch (error) {
      toast({
        title: 'Export failed',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive',
      })
    }
  }

  // Handle import
  const handleImport = async () => {
    if (!selectedTable || !importFile) return

    try {
      const result = await importDataMutation.mutateAsync({
        file: importFile,
        table: selectedTable,
        format: importFormat,
        mode: importMode,
        skipErrors: true,
      })

      toast({
        title: 'Import complete',
        description: `Imported ${result.rows_imported} rows${result.rows_skipped > 0 ? `, skipped ${result.rows_skipped}` : ''}`,
      })
      setImportDialogOpen(false)
      setImportFile(null)
      refetchData()
    } catch (error) {
      toast({
        title: 'Import failed',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive',
      })
    }
  }

  // Handle bulk delete
  const handleBulkDelete = async () => {
    if (!selectedTable || selectedRows.length === 0) return

    try {
      const result = await bulkDeleteMutation.mutateAsync({
        table: selectedTable,
        ids: selectedRows,
        id_column: idColumn,
        cascade: cascadeDelete,
      })

      toast({
        title: 'Delete complete',
        description: `Deleted ${result.rows_affected} rows${cascadeDelete ? ' (including related records)' : ''}`,
      })
      setDeleteDialogOpen(false)
      setCascadeDelete(false)
      setSelectedRows([])
      refetchData()
    } catch (error) {
      toast({
        title: 'Delete failed',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive',
      })
    }
  }

  // Handle bulk update
  const handleBulkUpdate = async () => {
    if (!selectedTable || selectedRows.length === 0 || !bulkUpdateColumn || bulkUpdateValue === '') return

    try {
      const result = await bulkUpdateMutation.mutateAsync({
        table: selectedTable,
        ids: selectedRows,
        id_column: idColumn,
        updates: { [bulkUpdateColumn]: bulkUpdateValue },
      })

      toast({
        title: 'Bulk update complete',
        description: `Updated ${result.rows_affected} rows`,
      })
      setBulkUpdateDialogOpen(false)
      setBulkUpdateColumn('')
      setBulkUpdateValue('')
      setSelectedRows([])
      refetchData()
    } catch (error) {
      toast({
        title: 'Bulk update failed',
        description: error instanceof Error ? error.message : 'Unknown error',
        variant: 'destructive',
      })
    }
  }

  // Clear all filters
  const clearFilters = () => {
    setFilters([])
    setPage(1)
  }

  // Add a new filter
  const addFilter = () => {
    setFilters((prev) => [...prev, { id: generateFilterId(), column: '', operator: 'equals', value: '' }])
  }

  // Update a specific filter
  const updateFilter = (id: string, updates: Partial<FilterState>) => {
    setFilters((prev) => prev.map((f) => (f.id === id ? { ...f, ...updates } : f)))
    setPage(1)
  }

  // Remove a specific filter
  const removeFilter = (id: string) => {
    setFilters((prev) => prev.filter((f) => f.id !== id))
    setPage(1)
  }

  // Check if we have active filters (some operators don't need a value)
  const hasActiveFilters = filterConditions.length > 0

  // Mobile sidebar state
  const [sidebarOpen, setSidebarOpen] = useState(false)

  // Stable callback for search input to prevent focus loss
  const handleTableSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setTableSearch(e.target.value)
  }, [])

  // Render table items - memoized to prevent unnecessary re-renders
  const renderTableItems = useCallback(
    (onSelect?: () => void) => (
      <ScrollArea className="flex-1">
        <div className="space-y-1 p-2">
          {tablesLoading
            ? [...Array(10)].map((_, i) => <Skeleton key={i} className="h-14 rounded-lg" />)
            : filteredTables.map((table) => {
                const colors = getTableTypeColor(table.name)
                return (
                  <button
                    key={table.name}
                    onClick={() => {
                      setSelectedTable(table.name)
                      setPage(1)
                      setSelectedRows([])
                      onSelect?.()
                    }}
                    className={cn(
                      'w-full p-2.5 rounded-lg text-left transition-all',
                      'hover:bg-muted/50',
                      selectedTable === table.name && 'bg-muted ring-1 ring-border',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Table2 className={cn('h-4 w-4 shrink-0', colors.text)} />
                      <span className="truncate text-sm font-medium">{table.name}</span>
                    </div>
                    <div className="text-xs text-muted-foreground mt-1 pl-6 flex items-center gap-2">
                      <span>{table.row_count.toLocaleString()} rows</span>
                      <span className="text-muted-foreground/50">•</span>
                      <span>{table.size_human}</span>
                    </div>
                  </button>
                )
              })}
        </div>
      </ScrollArea>
    ),
    [tablesLoading, filteredTables, selectedTable, setSelectedTable, setPage, setSelectedRows],
  )

  return (
    <div className="flex flex-col md:flex-row gap-3 md:gap-4 h-[calc(100vh-200px)] md:h-[calc(100vh-260px)] min-h-[400px] md:min-h-[500px]">
      {/* Mobile Table Selector Sheet */}
      <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
        <SheetContent side="left" className="w-[280px] p-0 flex flex-col">
          <SheetHeader className="p-4 pb-2 shrink-0">
            <SheetTitle className="text-sm font-medium flex items-center gap-2">
              <Table2 className="h-4 w-4" />
              Tables
            </SheetTitle>
          </SheetHeader>
          <div className="relative p-2">
            <Search className="absolute left-4 top-4 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder="Search tables..."
              value={tableSearch}
              onChange={handleTableSearchChange}
              className="pl-8 h-9"
            />
          </div>
          {renderTableItems(() => setSidebarOpen(false))}
        </SheetContent>
      </Sheet>

      {/* Desktop Table List Sidebar - Hidden on mobile */}
      <div className="hidden md:flex w-60 shrink-0 flex-col">
        <Card className="flex-1 bg-card/50 border-border/50 flex flex-col overflow-hidden">
          <CardHeader className="pb-2 shrink-0">
            <CardTitle className="text-sm font-medium">Tables</CardTitle>
          </CardHeader>
          <CardContent className="p-0 flex-1 overflow-hidden flex flex-col">
            <div className="relative">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Search tables..."
                value={tableSearch}
                onChange={handleTableSearchChange}
                className="pl-8 h-9"
              />
            </div>
            {renderTableItems()}
          </CardContent>
        </Card>
      </div>

      {/* Main Content */}
      <div className="flex-1 min-w-0 flex flex-col gap-3">
        {!selectedTable ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            <div className="text-center">
              <Database className="h-12 w-12 md:h-16 md:w-16 mx-auto mb-4 opacity-30" />
              <p className="text-base md:text-lg font-medium">Select a table to browse</p>
              <p className="text-xs md:text-sm text-muted-foreground mt-1 px-4">
                Choose a table from the sidebar to view and edit data
              </p>
              {/* Mobile table selector button */}
              <Button variant="outline" className="mt-4 md:hidden gap-2" onClick={() => setSidebarOpen(true)}>
                <Table2 className="h-4 w-4" />
                Select Table
              </Button>
            </div>
          </div>
        ) : (
          <>
            {/* FK Navigation Breadcrumb */}
            {navigationStack.length > 0 && (
              <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-muted/30 border border-border/50 shrink-0 overflow-x-auto text-xs">
                <Network className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                {navigationStack.map((entry, index) => (
                  <span key={index} className="flex items-center gap-1.5 shrink-0">
                    <button
                      onClick={() => handleBreadcrumbNavigate(index)}
                      className="text-blue-400 hover:text-blue-300 hover:underline transition-colors font-mono"
                    >
                      {entry.label}
                    </button>
                    <ChevronRight className="h-3 w-3 text-muted-foreground" />
                  </span>
                ))}
                <span className="font-mono font-medium text-foreground shrink-0">{selectedTable}</span>
                {filters.length > 0 && filters[0]?.value && (
                  <Badge variant="outline" className="text-[10px] px-1.5 ml-1 shrink-0">
                    {filters[0].column} = {filters[0].value}
                  </Badge>
                )}
                <button
                  onClick={handleClearNavigation}
                  className="ml-auto text-muted-foreground hover:text-foreground transition-colors shrink-0"
                  title="Clear navigation"
                >
                  <XCircle className="h-3.5 w-3.5" />
                </button>
              </div>
            )}

            {/* Toolbar Row 1 - Title and Actions */}
            <div className="flex items-center justify-between shrink-0 gap-2 md:gap-4 flex-wrap">
              <div className="flex items-center gap-2 md:gap-3 min-w-0">
                {/* Mobile menu button */}
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8 md:hidden shrink-0"
                  onClick={() => setSidebarOpen(true)}
                >
                  <Menu className="h-4 w-4" />
                </Button>
                <h3 className="font-semibold text-base md:text-lg truncate">{selectedTable}</h3>
                <Badge variant="secondary" className="font-mono text-xs shrink-0">
                  {tableData?.total.toLocaleString() || 0}
                </Badge>
                {hasActiveFilters && (
                  <Badge variant="outline" className="text-primary border-primary/30 text-xs shrink-0 hidden sm:flex">
                    Filtered
                  </Badge>
                )}
              </div>

              {/* Desktop actions */}
              <div className="hidden md:flex items-center gap-2 flex-wrap">
                <Button variant="outline" size="sm" onClick={() => setShowSchema(!showSchema)} className="gap-1.5">
                  <Eye className="h-4 w-4" />
                  {showSchema ? 'Hide' : 'Schema'}
                </Button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="sm" className="gap-1.5">
                      <Download className="h-4 w-4" />
                      Export
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={() => {
                        setExportFormat('csv')
                        setExportDialogOpen(true)
                      }}
                    >
                      <FileText className="h-4 w-4 mr-2" />
                      Export as CSV
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setExportFormat('json')
                        setExportDialogOpen(true)
                      }}
                    >
                      <FileJson className="h-4 w-4 mr-2" />
                      Export as JSON
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        setExportFormat('sql')
                        setExportDialogOpen(true)
                      }}
                    >
                      <Database className="h-4 w-4 mr-2" />
                      Export as SQL
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>

                <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setImportDialogOpen(true)}>
                  <Upload className="h-4 w-4" />
                  Import
                </Button>

                {selectedRows.length > 0 && (
                  <>
                    <Button
                      variant="outline"
                      size="sm"
                      className="gap-1.5"
                      onClick={() => setBulkUpdateDialogOpen(true)}
                    >
                      <Wand2 className="h-4 w-4" />
                      Update ({selectedRows.length})
                    </Button>
                    <Button
                      variant="destructive"
                      size="sm"
                      className="gap-1.5"
                      onClick={() => setDeleteDialogOpen(true)}
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete ({selectedRows.length})
                    </Button>
                  </>
                )}

                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => refetchData()}>
                  <RefreshCw className="h-4 w-4" />
                </Button>
              </div>

              {/* Mobile actions dropdown */}
              <div className="flex md:hidden items-center gap-1">
                {selectedRows.length > 0 && (
                  <Badge variant="secondary" className="text-xs">
                    {selectedRows.length}
                  </Badge>
                )}
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => refetchData()}>
                  <RefreshCw className="h-4 w-4" />
                </Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline" size="icon" className="h-8 w-8">
                      <MoreVertical className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-48">
                    <DropdownMenuItem onClick={() => setShowSchema(!showSchema)}>
                      <Eye className="h-4 w-4 mr-2" />
                      {showSchema ? 'Hide Schema' : 'Show Schema'}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        setExportFormat('csv')
                        setExportDialogOpen(true)
                      }}
                    >
                      <Download className="h-4 w-4 mr-2" />
                      Export CSV
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setExportFormat('json')
                        setExportDialogOpen(true)
                      }}
                    >
                      <FileJson className="h-4 w-4 mr-2" />
                      Export JSON
                    </DropdownMenuItem>
                    <DropdownMenuItem onClick={() => setImportDialogOpen(true)}>
                      <Upload className="h-4 w-4 mr-2" />
                      Import
                    </DropdownMenuItem>
                    {selectedRows.length > 0 && (
                      <>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem onClick={() => setBulkUpdateDialogOpen(true)}>
                          <Wand2 className="h-4 w-4 mr-2" />
                          Update {selectedRows.length} rows
                        </DropdownMenuItem>
                        <DropdownMenuItem onClick={() => setDeleteDialogOpen(true)} className="text-destructive">
                          <Trash2 className="h-4 w-4 mr-2" />
                          Delete {selectedRows.length} rows
                        </DropdownMenuItem>
                      </>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>

            {/* Toolbar Row 2 - Filters */}
            <div className="flex flex-col gap-2 shrink-0">
              {/* Existing filters */}
              {filters.map((filter) => (
                <div key={filter.id} className="flex items-center gap-1.5 md:gap-2 flex-wrap">
                  <Select
                    value={filter.column || '__none__'}
                    onValueChange={(v) =>
                      updateFilter(filter.id, {
                        column: v === '__none__' ? '' : v,
                        operator: 'equals',
                        value: '',
                      })
                    }
                  >
                    <SelectTrigger className="w-[140px] md:w-[180px] h-9 text-xs md:text-sm">
                      <Filter className="h-3.5 w-3.5 mr-1.5 shrink-0" />
                      <SelectValue placeholder="Filter column..." />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__">Select column...</SelectItem>
                      {schema?.columns.map((col) => (
                        <SelectItem key={col.name} value={col.name}>
                          <span className="font-mono text-xs">{col.name}</span>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  {filter.column && (
                    <>
                      <Select
                        value={filter.operator}
                        onValueChange={(v) => updateFilter(filter.id, { operator: v, value: '' })}
                      >
                        <SelectTrigger className="w-[110px] md:w-[140px] h-9 text-xs md:text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="equals">= equals</SelectItem>
                          <SelectItem value="not_equals">≠ not equals</SelectItem>
                          <SelectItem value="contains">∋ contains</SelectItem>
                          <SelectItem value="starts_with">starts with</SelectItem>
                          <SelectItem value="ends_with">ends with</SelectItem>
                          <SelectItem value="is_null">is NULL</SelectItem>
                          <SelectItem value="is_not_null">is NOT NULL</SelectItem>
                          <SelectItem value="gt">&gt; greater</SelectItem>
                          <SelectItem value="gte">≥ greater or eq</SelectItem>
                          <SelectItem value="lt">&lt; less</SelectItem>
                          <SelectItem value="lte">≤ less or eq</SelectItem>
                          <SelectItem value="array_contains">[] contains</SelectItem>
                          <SelectItem value="array_empty">[] is empty</SelectItem>
                          <SelectItem value="array_not_empty">[] not empty</SelectItem>
                          <SelectItem value="json_is_null">JSON is null</SelectItem>
                          <SelectItem value="json_is_not_null">JSON not null</SelectItem>
                        </SelectContent>
                      </Select>

                      {operatorNeedsValue(filter.operator) && (
                        <Input
                          placeholder="Filter value..."
                          value={filter.value}
                          onChange={(e) => updateFilter(filter.id, { value: e.target.value })}
                          className="w-[120px] md:w-[160px] h-9 text-xs md:text-sm"
                        />
                      )}
                    </>
                  )}

                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => removeFilter(filter.id)}
                    className="h-9 w-9 p-0 text-muted-foreground hover:text-destructive"
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}

              {/* Add filter / Clear all buttons */}
              <div className="flex items-center gap-1.5 md:gap-2">
                <Button variant="outline" size="sm" onClick={addFilter} className="h-9 gap-1.5 text-xs md:text-sm">
                  <Filter className="h-3.5 w-3.5" />
                  Add Filter
                </Button>

                {hasActiveFilters && (
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={clearFilters}
                    className="h-9 gap-1.5 text-muted-foreground"
                  >
                    <XCircle className="h-4 w-4" />
                    <span className="hidden sm:inline">Clear All</span>
                  </Button>
                )}
              </div>
            </div>

            <div className="flex flex-col md:flex-row gap-3 md:gap-4 flex-1 min-h-0 overflow-hidden">
              {/* Schema Panel - Hidden on mobile by default */}
              {showSchema && (
                <Card className="hidden md:flex w-72 shrink-0 bg-card/50 border-border/50 flex-col overflow-hidden">
                  <CardHeader className="pb-2 shrink-0">
                    <CardTitle className="text-sm font-medium">Schema</CardTitle>
                  </CardHeader>
                  <CardContent className="p-2 flex-1 overflow-hidden">
                    <ScrollArea className="h-full">
                      <div className="pr-2">
                        {schemaLoading ? (
                          <div className="space-y-2">
                            {[...Array(8)].map((_, i) => (
                              <Skeleton key={i} className="h-10 rounded-lg" />
                            ))}
                          </div>
                        ) : (
                          <SchemaViewer schema={schema} onNavigateTable={handleNavigateToTable} />
                        )}
                      </div>
                    </ScrollArea>
                  </CardContent>
                </Card>
              )}

              {/* Mobile Schema Collapsible */}
              {showSchema && (
                <Collapsible className="md:hidden">
                  <Card className="bg-card/50 border-border/50">
                    <CollapsibleTrigger className="w-full">
                      <CardHeader className="pb-2 flex flex-row items-center justify-between">
                        <CardTitle className="text-sm font-medium">Schema</CardTitle>
                        <ChevronDown className="h-4 w-4" />
                      </CardHeader>
                    </CollapsibleTrigger>
                    <CollapsibleContent>
                      <CardContent className="pt-0">
                        {schemaLoading ? (
                          <div className="space-y-2">
                            {[...Array(4)].map((_, i) => (
                              <Skeleton key={i} className="h-8 rounded-lg" />
                            ))}
                          </div>
                        ) : (
                          <SchemaViewer schema={schema} onNavigateTable={handleNavigateToTable} />
                        )}
                      </CardContent>
                    </CollapsibleContent>
                  </Card>
                </Collapsible>
              )}

              {/* Data Grid */}
              <Card className="flex-1 min-w-0 bg-card/50 border-border/50 flex flex-col overflow-hidden">
                <CardContent className="p-0 flex-1 flex flex-col overflow-hidden">
                  {/* Table container with horizontal scroll */}
                  <div className="flex-1 overflow-auto relative">
                    <table className="w-full text-xs md:text-sm border-collapse min-w-max">
                      <thead className="sticky top-0 z-20 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
                        <tr className="border-b border-border/50">
                          {/* Sticky checkbox column */}
                          <th className="w-12 p-3 text-left sticky left-0 z-30 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
                            <Checkbox
                              checked={selectedRows.length === tableData?.rows.length && tableData?.rows.length > 0}
                              onCheckedChange={handleSelectAll}
                            />
                          </th>
                          {/* Sticky actions column */}
                          <th className="w-16 p-3 text-center sticky left-12 z-30 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 border-r border-border/30">
                            <span className="sr-only">Actions</span>
                          </th>
                          {tableData?.columns.map((col) => (
                            <th key={col} className="p-3 text-left font-medium whitespace-nowrap">
                              <button
                                className="flex items-center gap-1.5 hover:text-foreground transition-colors group"
                                onClick={() => handleSort(col)}
                              >
                                <span>{col}</span>
                                {orderBy === col ? (
                                  orderDir === 'asc' ? (
                                    <SortAsc className="h-4 w-4 text-primary" />
                                  ) : (
                                    <SortDesc className="h-4 w-4 text-primary" />
                                  )
                                ) : (
                                  <ArrowUpDown className="h-4 w-4 opacity-0 group-hover:opacity-50 transition-opacity" />
                                )}
                              </button>
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {dataLoading ? (
                          [...Array(10)].map((_, i) => (
                            <tr key={i} className="border-b border-border/30">
                              <td className="p-3 sticky left-0 bg-card/95">
                                <Skeleton className="h-4 w-4" />
                              </td>
                              <td className="p-3 sticky left-12 bg-card/95">
                                <Skeleton className="h-4 w-4" />
                              </td>
                              {[...Array(5)].map((_, j) => (
                                <td key={j} className="p-3">
                                  <Skeleton className="h-4 w-24" />
                                </td>
                              ))}
                            </tr>
                          ))
                        ) : tableData?.rows.length === 0 ? (
                          <tr>
                            <td
                              colSpan={(tableData?.columns.length || 0) + 2}
                              className="p-8 text-center text-muted-foreground"
                            >
                              No data found
                            </td>
                          </tr>
                        ) : (
                          tableData?.rows.map((row, i) => {
                            const rowId = String(row[idColumn])
                            return (
                              <tr
                                key={i}
                                className={cn(
                                  'border-b border-border/30 hover:bg-muted/30 transition-colors',
                                  selectedRows.includes(rowId) && 'bg-muted/50',
                                )}
                              >
                                {/* Sticky checkbox */}
                                <td className="p-3 sticky left-0 z-10 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
                                  <Checkbox
                                    checked={selectedRows.includes(rowId)}
                                    onCheckedChange={() => handleRowSelect(rowId)}
                                  />
                                </td>
                                {/* Sticky actions */}
                                <td className="p-3 sticky left-12 z-10 bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80 border-r border-border/30">
                                  <div className="flex items-center justify-center gap-1">
                                    <TooltipProvider>
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-7 w-7"
                                            onClick={() => handleEditRow(row)}
                                          >
                                            <Pencil className="h-3.5 w-3.5" />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>Edit row</TooltipContent>
                                      </Tooltip>
                                    </TooltipProvider>
                                    <TooltipProvider>
                                      <Tooltip>
                                        <TooltipTrigger asChild>
                                          <Button
                                            variant="ghost"
                                            size="icon"
                                            className="h-7 w-7"
                                            onClick={() => handleViewRelated(row)}
                                          >
                                            <Network className="h-3.5 w-3.5" />
                                          </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>View related records</TooltipContent>
                                      </Tooltip>
                                    </TooltipProvider>
                                  </div>
                                </td>
                                {/* Data cells */}
                                {tableData?.columns.map((col) => (
                                  <td key={col} className="p-3">
                                    <DataCell
                                      value={row[col]}
                                      column={col}
                                      columnInfo={columnInfoMap.get(col)}
                                      onNavigate={handleFKNavigate}
                                    />
                                  </td>
                                ))}
                              </tr>
                            )
                          })
                        )}
                      </tbody>
                    </table>
                  </div>

                  {/* Pagination - Responsive */}
                  <div className="flex items-center justify-between p-2 md:p-3 border-t border-border/50 shrink-0 bg-card/50 gap-2">
                    <div className="hidden sm:flex items-center gap-2">
                      <span className="text-xs md:text-sm text-muted-foreground">Rows:</span>
                      <Select
                        value={String(perPage)}
                        onValueChange={(v) => {
                          setPerPage(Number(v))
                          setPage(1)
                        }}
                      >
                        <SelectTrigger className="w-[60px] md:w-[70px] h-7 md:h-8 text-xs md:text-sm">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="10">10</SelectItem>
                          <SelectItem value="25">25</SelectItem>
                          <SelectItem value="50">50</SelectItem>
                          <SelectItem value="100">100</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>

                    <div className="flex items-center gap-2 md:gap-4 flex-1 sm:flex-none justify-between sm:justify-end">
                      <span className="text-xs md:text-sm text-muted-foreground">
                        {page}/{tableData?.pages || 1}
                      </span>
                      <div className="flex items-center gap-1">
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-7 w-7 md:h-8 md:w-8"
                          disabled={page === 1}
                          onClick={() => setPage(page - 1)}
                        >
                          <ChevronLeft className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="outline"
                          size="icon"
                          className="h-7 w-7 md:h-8 md:w-8"
                          disabled={page === (tableData?.pages || 1)}
                          onClick={() => setPage(page + 1)}
                        >
                          <ChevronRight className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>
          </>
        )}
      </div>

      {/* Edit Row Dialog */}
      {editingRow && schema && (
        <EditRowDialog
          open={editDialogOpen}
          onOpenChange={(open) => {
            setEditDialogOpen(open)
            if (!open) setEditingRow(null)
          }}
          tableName={selectedTable || ''}
          columns={schema.columns}
          rowData={editingRow}
          idColumn={idColumn}
          onSave={handleSaveEdit}
          isPending={bulkUpdateMutation.isPending}
        />
      )}

      {/* Related Records Panel */}
      <RelatedRecordsPanel
        open={relatedPanelOpen}
        onOpenChange={(open) => {
          setRelatedPanelOpen(open)
          if (!open) setRelatedRow(null)
        }}
        tableName={selectedTable || ''}
        rowId={relatedRowId || ''}
        idColumn={idColumn}
        data={relatedData ?? null}
        isLoading={relatedLoading}
        onNavigate={(table, column, value) => {
          setRelatedPanelOpen(false)
          setRelatedRow(null)
          handleFKNavigate(table, column, value)
        }}
      />

      {/* Export Dialog */}
      <Dialog open={exportDialogOpen} onOpenChange={setExportDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Export {selectedTable}</DialogTitle>
            <DialogDescription>Export table data in {exportFormat.toUpperCase()} format</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            {exportFormat === 'sql' && (
              <>
                <div className="flex items-center justify-between">
                  <Label>Include schema (CREATE TABLE)</Label>
                  <Checkbox checked={exportIncludeSchema} onCheckedChange={(c) => setExportIncludeSchema(!!c)} />
                </div>
                <div className="flex items-center justify-between">
                  <Label>Include data (INSERT statements)</Label>
                  <Checkbox checked={exportIncludeData} onCheckedChange={(c) => setExportIncludeData(!!c)} />
                </div>
              </>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setExportDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleExport} disabled={exportMutation.isPending}>
              {exportMutation.isPending ? 'Exporting...' : 'Export'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Import Dialog */}
      <Dialog open={importDialogOpen} onOpenChange={setImportDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import to {selectedTable}</DialogTitle>
            <DialogDescription>Upload a file to import data into the table</DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div>
              <Label>File</Label>
              <Input
                type="file"
                accept=".csv,.json,.sql"
                onChange={(e) => {
                  const file = e.target.files?.[0]
                  if (file) {
                    setImportFile(file)
                    // Auto-detect format
                    const ext = file.name.split('.').pop()?.toLowerCase()
                    if (ext === 'csv' || ext === 'json' || ext === 'sql') {
                      setImportFormat(ext as 'csv' | 'json' | 'sql')
                    }
                  }
                }}
                className="mt-1"
              />
            </div>

            <div>
              <Label>Format</Label>
              <Select value={importFormat} onValueChange={(v) => setImportFormat(v as 'csv' | 'json' | 'sql')}>
                <SelectTrigger className="mt-1">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="csv">CSV</SelectItem>
                  <SelectItem value="json">JSON</SelectItem>
                  <SelectItem value="sql">SQL</SelectItem>
                </SelectContent>
              </Select>
            </div>

            {importFormat !== 'sql' && (
              <div>
                <Label>Import Mode</Label>
                <Select value={importMode} onValueChange={(v) => setImportMode(v as 'insert' | 'upsert' | 'replace')}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="insert">Insert (new rows only)</SelectItem>
                    <SelectItem value="upsert">Upsert (insert or update)</SelectItem>
                    <SelectItem value="replace">Replace (delete and insert)</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setImportDialogOpen(false)}>
              Cancel
            </Button>
            <Button onClick={handleImport} disabled={!importFile || importDataMutation.isPending}>
              {importDataMutation.isPending ? 'Importing...' : 'Import'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete Confirmation */}
      <AlertDialog
        open={deleteDialogOpen}
        onOpenChange={(open) => {
          setDeleteDialogOpen(open)
          if (!open) setCascadeDelete(false)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {selectedRows.length} rows?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. This will permanently delete the selected rows from {selectedTable}.
            </AlertDialogDescription>
          </AlertDialogHeader>

          <div className="flex items-center gap-3 py-2 px-1">
            <Checkbox id="cascade-delete" checked={cascadeDelete} onCheckedChange={(c) => setCascadeDelete(!!c)} />
            <div className="grid gap-1">
              <Label htmlFor="cascade-delete" className="font-medium text-sm cursor-pointer">
                Cascade delete
              </Label>
              <p className="text-xs text-muted-foreground">
                Also delete related records in child tables (if any foreign key references exist)
              </p>
            </div>
          </div>

          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {bulkDeleteMutation.isPending ? 'Deleting...' : cascadeDelete ? 'Delete All' : 'Delete'}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Bulk Update Dialog */}
      <Dialog open={bulkUpdateDialogOpen} onOpenChange={setBulkUpdateDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Bulk Update {selectedRows.length} rows</DialogTitle>
            <DialogDescription>
              Set a new value for a column across all selected rows in {selectedTable}.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 py-4">
            <div>
              <Label>Column to update</Label>
              <Select value={bulkUpdateColumn} onValueChange={setBulkUpdateColumn}>
                <SelectTrigger className="mt-1">
                  <SelectValue placeholder="Select column..." />
                </SelectTrigger>
                <SelectContent>
                  {schema?.columns
                    .filter((c) => !c.is_primary_key && c.name !== 'created_at' && c.name !== 'updated_at')
                    .map((col) => (
                      <SelectItem key={col.name} value={col.name}>
                        <span className="font-mono">{col.name}</span>
                        <span className="text-muted-foreground ml-2 text-xs">({col.data_type})</span>
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>

            {bulkUpdateColumn && (
              <div>
                <Label>New value</Label>
                <Input
                  value={bulkUpdateValue}
                  onChange={(e) => setBulkUpdateValue(e.target.value)}
                  placeholder={`Enter new value for ${bulkUpdateColumn}...`}
                  className="mt-1 font-mono"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  This will update {selectedRows.length} row(s) with the new value.
                </p>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setBulkUpdateDialogOpen(false)}>
              Cancel
            </Button>
            <Button
              onClick={handleBulkUpdate}
              disabled={!bulkUpdateColumn || bulkUpdateValue === '' || bulkUpdateMutation.isPending}
            >
              <Wand2 className="h-4 w-4 mr-2" />
              {bulkUpdateMutation.isPending ? 'Updating...' : 'Update All'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
