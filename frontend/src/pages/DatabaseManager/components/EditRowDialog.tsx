import { useState, useEffect, useMemo } from 'react'
import { Save, X, ToggleLeft, Calendar, Hash, Type, Braces, AlertCircle } from 'lucide-react'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import type { ColumnInfo } from '../types'

interface EditRowDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  tableName: string
  columns: ColumnInfo[]
  rowData: Record<string, unknown>
  idColumn: string
  onSave: (updates: Record<string, unknown>) => Promise<void>
  isPending?: boolean
}

// Get field type from PostgreSQL data type
function getFieldType(dataType: string): 'text' | 'number' | 'boolean' | 'json' | 'timestamp' | 'textarea' {
  const lowerType = dataType.toLowerCase()

  if (lowerType.includes('bool')) return 'boolean'
  if (
    lowerType.includes('int') ||
    lowerType.includes('numeric') ||
    lowerType.includes('float') ||
    lowerType.includes('decimal') ||
    lowerType.includes('real') ||
    lowerType.includes('double')
  )
    return 'number'
  if (lowerType.includes('json') || lowerType.includes('array')) return 'json'
  if (lowerType.includes('timestamp') || lowerType.includes('date') || lowerType.includes('time')) return 'timestamp'
  if (lowerType.includes('text') && !lowerType.includes('varchar')) return 'textarea'

  return 'text'
}

// Get icon for field type
function FieldTypeIcon({ type }: { type: ReturnType<typeof getFieldType> }) {
  switch (type) {
    case 'boolean':
      return <ToggleLeft className="h-4 w-4" />
    case 'number':
      return <Hash className="h-4 w-4" />
    case 'json':
      return <Braces className="h-4 w-4" />
    case 'timestamp':
      return <Calendar className="h-4 w-4" />
    default:
      return <Type className="h-4 w-4" />
  }
}

// Individual field editor component
function FieldEditor({
  column,
  value,
  onChange,
  disabled,
}: {
  column: ColumnInfo
  value: unknown
  onChange: (value: unknown) => void
  disabled?: boolean
}) {
  const fieldType = getFieldType(column.data_type)
  const isNull = value === null || value === undefined
  const [isNullChecked, setIsNullChecked] = useState(isNull)
  const [localValue, setLocalValue] = useState<string>(() => {
    if (isNull) return ''
    if (typeof value === 'object') return JSON.stringify(value, null, 2)
    return String(value)
  })

  // Update local value when external value changes
  useEffect(() => {
    if (value === null || value === undefined) {
      setIsNullChecked(true)
      setLocalValue('')
    } else {
      setIsNullChecked(false)
      if (typeof value === 'object') {
        setLocalValue(JSON.stringify(value, null, 2))
      } else {
        setLocalValue(String(value))
      }
    }
  }, [value])

  const handleNullToggle = (checked: boolean) => {
    setIsNullChecked(checked)
    if (checked) {
      onChange(null)
    } else {
      // Restore with default value based on type
      switch (fieldType) {
        case 'boolean':
          onChange(false)
          break
        case 'number':
          onChange(0)
          break
        case 'json':
          onChange({})
          break
        default:
          onChange('')
      }
    }
  }

  const handleChange = (newValue: string) => {
    setLocalValue(newValue)

    if (isNullChecked) return

    switch (fieldType) {
      case 'boolean':
        onChange(newValue === 'true')
        break
      case 'number':
        const num = parseFloat(newValue)
        onChange(isNaN(num) ? null : num)
        break
      case 'json':
        try {
          onChange(JSON.parse(newValue))
        } catch {
          // Keep as string if invalid JSON
        }
        break
      default:
        onChange(newValue)
    }
  }

  // Read-only fields (typically auto-generated)
  const isReadOnly =
    disabled ||
    column.is_primary_key ||
    column.name === 'created_at' ||
    column.name === 'updated_at' ||
    column.default_value?.includes('nextval')

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <FieldTypeIcon type={fieldType} />
          <Label className="font-mono text-sm">{column.name}</Label>
          <Badge variant="outline" className="text-xs font-mono">
            {column.data_type}
          </Badge>
          {!column.is_nullable && (
            <Badge variant="outline" className="text-xs text-rose-400 border-rose-500/30">
              Required
            </Badge>
          )}
          {isReadOnly && (
            <Badge variant="secondary" className="text-xs">
              Read-only
            </Badge>
          )}
        </div>

        {column.is_nullable && !isReadOnly && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">NULL</span>
            <Switch checked={isNullChecked} onCheckedChange={handleNullToggle} disabled={isReadOnly} />
          </div>
        )}
      </div>

      {/* Field input based on type */}
      {fieldType === 'boolean' ? (
        <div className="flex items-center gap-3">
          <Switch
            checked={!isNullChecked && localValue === 'true'}
            onCheckedChange={(checked) => {
              if (!isNullChecked) {
                setLocalValue(String(checked))
                onChange(checked)
              }
            }}
            disabled={isReadOnly || isNullChecked}
          />
          <span className="text-sm text-muted-foreground">
            {isNullChecked ? 'NULL' : localValue === 'true' ? 'True' : 'False'}
          </span>
        </div>
      ) : fieldType === 'json' || fieldType === 'textarea' ? (
        <Textarea
          value={isNullChecked ? '' : localValue}
          onChange={(e) => handleChange(e.target.value)}
          disabled={isReadOnly || isNullChecked}
          placeholder={isNullChecked ? 'NULL' : fieldType === 'json' ? '{ }' : ''}
          className={cn('font-mono text-sm min-h-[100px]', isNullChecked && 'bg-muted/50 text-muted-foreground italic')}
        />
      ) : fieldType === 'timestamp' ? (
        <Input
          type="datetime-local"
          value={isNullChecked ? '' : localValue?.replace(' ', 'T')?.slice(0, 16) || ''}
          onChange={(e) => handleChange(e.target.value.replace('T', ' '))}
          disabled={isReadOnly || isNullChecked}
          className={cn('font-mono', isNullChecked && 'bg-muted/50 text-muted-foreground italic')}
        />
      ) : fieldType === 'number' ? (
        <Input
          type="number"
          value={isNullChecked ? '' : localValue}
          onChange={(e) => handleChange(e.target.value)}
          disabled={isReadOnly || isNullChecked}
          placeholder={isNullChecked ? 'NULL' : '0'}
          className={cn('font-mono', isNullChecked && 'bg-muted/50 text-muted-foreground italic')}
        />
      ) : (
        <Input
          type="text"
          value={isNullChecked ? '' : localValue}
          onChange={(e) => handleChange(e.target.value)}
          disabled={isReadOnly || isNullChecked}
          placeholder={isNullChecked ? 'NULL' : ''}
          className={cn(isNullChecked && 'bg-muted/50 text-muted-foreground italic')}
        />
      )}
    </div>
  )
}

export function EditRowDialog({
  open,
  onOpenChange,
  tableName,
  columns,
  rowData,
  idColumn,
  onSave,
  isPending = false,
}: EditRowDialogProps) {
  const [editedData, setEditedData] = useState<Record<string, unknown>>({})
  const [error, setError] = useState<string | null>(null)

  // Initialize edited data when dialog opens
  useEffect(() => {
    if (open && rowData) {
      setEditedData({ ...rowData })
      setError(null)
    }
  }, [open, rowData])

  // Get the row ID for display
  const rowId = rowData?.[idColumn]

  // Calculate which fields have been modified
  const modifiedFields = useMemo(() => {
    const modified: Record<string, unknown> = {}
    for (const key of Object.keys(editedData)) {
      const originalValue = rowData[key]
      const editedValue = editedData[key]

      // Handle JSON comparison
      if (typeof originalValue === 'object' && typeof editedValue === 'object') {
        if (JSON.stringify(originalValue) !== JSON.stringify(editedValue)) {
          modified[key] = editedValue
        }
      } else if (originalValue !== editedValue) {
        modified[key] = editedValue
      }
    }
    return modified
  }, [editedData, rowData])

  const hasChanges = Object.keys(modifiedFields).length > 0

  // Handle field change
  const handleFieldChange = (columnName: string, value: unknown) => {
    setEditedData((prev) => ({
      ...prev,
      [columnName]: value,
    }))
    setError(null)
  }

  // Handle save
  const handleSave = async () => {
    if (!hasChanges) return

    try {
      await onSave(modifiedFields)
      onOpenChange(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save changes')
    }
  }

  // Sort columns: primary key first, then by name
  const sortedColumns = useMemo(() => {
    return [...columns].sort((a, b) => {
      if (a.is_primary_key && !b.is_primary_key) return -1
      if (!a.is_primary_key && b.is_primary_key) return 1
      if (a.name === 'created_at') return 1
      if (b.name === 'created_at') return -1
      if (a.name === 'updated_at') return 1
      if (b.name === 'updated_at') return -1
      return a.name.localeCompare(b.name)
    })
  }, [columns])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] flex flex-col overflow-hidden">
        <DialogHeader className="shrink-0">
          <DialogTitle className="flex items-center gap-2">
            Edit Row
            <Badge variant="secondary" className="font-mono">
              {tableName}
            </Badge>
            {rowId !== undefined && (
              <Badge variant="outline" className="font-mono">
                {idColumn}: {String(rowId)}
              </Badge>
            )}
          </DialogTitle>
          <DialogDescription>Modify the field values below. Read-only fields cannot be edited.</DialogDescription>
        </DialogHeader>

        <div className="flex-1 min-h-0 overflow-y-auto pr-2 -mr-2">
          <div className="space-y-6 py-4">
            {sortedColumns.map((column) => (
              <FieldEditor
                key={column.name}
                column={column}
                value={editedData[column.name]}
                onChange={(value) => handleFieldChange(column.name, value)}
              />
            ))}
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 p-3 rounded-lg bg-destructive/10 text-destructive text-sm">
            <AlertCircle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        )}

        <DialogFooter className="flex items-center justify-between sm:justify-between shrink-0">
          <div className="text-sm text-muted-foreground">
            {hasChanges ? (
              <span className="text-primary">{Object.keys(modifiedFields).length} field(s) modified</span>
            ) : (
              'No changes'
            )}
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => onOpenChange(false)}>
              <X className="h-4 w-4 mr-2" />
              Cancel
            </Button>
            <Button onClick={handleSave} disabled={!hasChanges || isPending}>
              <Save className="h-4 w-4 mr-2" />
              {isPending ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
