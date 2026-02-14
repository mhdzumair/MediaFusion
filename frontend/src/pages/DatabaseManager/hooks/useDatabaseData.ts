import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  databaseApi,
  type TableDataParams,
  type OrphanCleanupRequest,
  type MaintenanceRequest,
  type BulkDeleteRequest,
  type BulkUpdateRequest,
} from '@/lib/api/admin'

// ============================================
// Query Keys
// ============================================

export const databaseQueryKeys = {
  all: ['database'] as const,
  stats: () => [...databaseQueryKeys.all, 'stats'] as const,
  tables: () => [...databaseQueryKeys.all, 'tables'] as const,
  tableSchema: (name: string) => [...databaseQueryKeys.all, 'schema', name] as const,
  tableData: (name: string, params?: TableDataParams) => 
    [...databaseQueryKeys.all, 'data', name, params] as const,
  orphans: () => [...databaseQueryKeys.all, 'orphans'] as const,
}

// ============================================
// Stats Hooks
// ============================================

export function useDatabaseStats() {
  return useQuery({
    queryKey: databaseQueryKeys.stats(),
    queryFn: async () => {
      return await databaseApi.getStats()
    },
    refetchInterval: 30000, // Refresh every 30 seconds
  })
}

// ============================================
// Tables Hooks
// ============================================

export function useTableList() {
  return useQuery({
    queryKey: databaseQueryKeys.tables(),
    queryFn: async () => {
      return await databaseApi.listTables()
    },
    staleTime: 60000, // Consider data fresh for 1 minute
  })
}

export function useTableSchema(tableName: string | null) {
  return useQuery({
    queryKey: databaseQueryKeys.tableSchema(tableName || ''),
    queryFn: async () => {
      if (!tableName) throw new Error('No table selected')
      return await databaseApi.getTableSchema(tableName)
    },
    enabled: !!tableName,
  })
}

export function useTableData(tableName: string | null, params: TableDataParams = {}) {
  return useQuery({
    queryKey: databaseQueryKeys.tableData(tableName || '', params),
    queryFn: async () => {
      if (!tableName) throw new Error('No table selected')
      return await databaseApi.getTableData(tableName, params)
    },
    enabled: !!tableName,
  })
}

// ============================================
// Export Hooks
// ============================================

export function useExportTable() {
  return useMutation({
    mutationFn: async ({
      tableName,
      format,
      options,
    }: {
      tableName: string
      format: 'csv' | 'json' | 'sql'
      options?: { include_schema?: boolean; include_data?: boolean; limit?: number }
    }) => {
      const blob = await databaseApi.exportTable(tableName, format, options)
      
      // Create download link
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${tableName}.${format}`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      
      return { success: true }
    },
  })
}

// ============================================
// Import Hooks
// ============================================

export function useImportPreview() {
  return useMutation({
    mutationFn: async ({
      file,
      table,
      format,
    }: {
      file: File
      table: string
      format: 'csv' | 'json' | 'sql'
    }) => {
      return await databaseApi.previewImport(file, table, format)
    },
  })
}

export function useImportData() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async ({
      file,
      table,
      format,
      mode,
      columnMapping,
      skipErrors,
    }: {
      file: File
      table: string
      format: 'csv' | 'json' | 'sql'
      mode: 'insert' | 'upsert' | 'replace'
      columnMapping?: Record<string, string>
      skipErrors?: boolean
    }) => {
      return await databaseApi.executeImport(file, table, format, mode, columnMapping, skipErrors)
    },
    onSuccess: (_, variables) => {
      // Invalidate table data after import
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tableData(variables.table),
      })
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

// ============================================
// Maintenance Hooks
// ============================================

export function useVacuumTables() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: MaintenanceRequest) => {
      return await databaseApi.vacuum(request)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

export function useAnalyzeTables() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: MaintenanceRequest) => {
      return await databaseApi.analyze(request)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

export function useReindexTables() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: MaintenanceRequest) => {
      return await databaseApi.reindex(request)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

// ============================================
// Orphan Hooks
// ============================================

export function useOrphanRecords() {
  return useQuery({
    queryKey: databaseQueryKeys.orphans(),
    queryFn: async () => {
      return await databaseApi.findOrphans()
    },
    staleTime: 60000,
  })
}

export function useCleanupOrphans() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: OrphanCleanupRequest = {}) => {
      return await databaseApi.cleanupOrphans(request)
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.orphans(),
      })
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

// ============================================
// Bulk Operations Hooks
// ============================================

export function useBulkDelete() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: BulkDeleteRequest) => {
      return await databaseApi.bulkDelete(request)
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tableData(variables.table),
      })
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tables(),
      })
    },
  })
}

export function useBulkUpdate() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: async (request: BulkUpdateRequest) => {
      return await databaseApi.bulkUpdate(request)
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({
        queryKey: databaseQueryKeys.tableData(variables.table),
      })
    },
  })
}

