import { useAuth } from '@/contexts/AuthContext'
import { Permission } from '@/types'

export function usePermission() {
  const { hasPermission } = useAuth()

  return {
    hasPermission,
    can: (permission: Permission) => hasPermission(permission),

    // Convenience methods for common permissions
    canViewDashboard: hasPermission(Permission.VIEW_DASHBOARD),
    canManageProfiles: hasPermission(Permission.MANAGE_PROFILES),
    canViewWatchHistory: hasPermission(Permission.VIEW_WATCH_HISTORY),
    canViewDownloads: hasPermission(Permission.VIEW_DOWNLOADS),
    canSubmitContribution: hasPermission(Permission.SUBMIT_CONTRIBUTION),
    canImportContent: hasPermission(Permission.IMPORT_CONTENT),
    canManageOwnRSS: hasPermission(Permission.MANAGE_OWN_RSS),

    // Moderator permissions
    canViewMetrics: hasPermission(Permission.VIEW_METRICS),
    canBlockTorrent: hasPermission(Permission.BLOCK_TORRENT),
    canDeleteTorrent: hasPermission(Permission.DELETE_TORRENT),
    canReviewContributions: hasPermission(Permission.REVIEW_CONTRIBUTIONS),
    canRunScrapers: hasPermission(Permission.RUN_SCRAPERS),
    canManageMetadata: hasPermission(Permission.MANAGE_METADATA),

    // Admin permissions
    canManageUsers: hasPermission(Permission.MANAGE_USERS),
    canAssignRoles: hasPermission(Permission.ASSIGN_ROLES),
    canViewAllRSS: hasPermission(Permission.VIEW_ALL_RSS),
    canManageAllRSS: hasPermission(Permission.MANAGE_ALL_RSS),
    canSystemConfig: hasPermission(Permission.SYSTEM_CONFIG),
  }
}
