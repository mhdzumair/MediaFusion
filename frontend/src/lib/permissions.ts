import { Permission, type UserRole } from '@/types'

// Role hierarchy (higher number = more permissions)
export const ROLE_HIERARCHY: Record<UserRole, number> = {
  user: 1,
  paid_user: 2,
  moderator: 3,
  admin: 4,
}

// Permissions by role
const ROLE_PERMISSIONS: Record<UserRole, Permission[]> = {
  user: [
    Permission.VIEW_DASHBOARD,
    Permission.MANAGE_PROFILES,
    Permission.VIEW_WATCH_HISTORY,
    Permission.VIEW_DOWNLOADS,
    Permission.SUBMIT_CONTRIBUTION,
    Permission.IMPORT_CONTENT,
    Permission.MANAGE_OWN_RSS,
  ],
  paid_user: [
    // Same as user for now, reserved for future premium features
    Permission.VIEW_DASHBOARD,
    Permission.MANAGE_PROFILES,
    Permission.VIEW_WATCH_HISTORY,
    Permission.VIEW_DOWNLOADS,
    Permission.SUBMIT_CONTRIBUTION,
    Permission.IMPORT_CONTENT,
    Permission.MANAGE_OWN_RSS,
  ],
  moderator: [
    // All user permissions plus moderator-specific
    Permission.VIEW_DASHBOARD,
    Permission.MANAGE_PROFILES,
    Permission.VIEW_WATCH_HISTORY,
    Permission.VIEW_DOWNLOADS,
    Permission.SUBMIT_CONTRIBUTION,
    Permission.IMPORT_CONTENT,
    Permission.MANAGE_OWN_RSS,
    // Moderator permissions
    Permission.VIEW_METRICS,
    Permission.BLOCK_TORRENT,
    Permission.DELETE_TORRENT,
    Permission.REVIEW_CONTRIBUTIONS,
    Permission.RUN_SCRAPERS,
    Permission.MANAGE_METADATA,
  ],
  admin: [
    // All permissions
    ...Object.values(Permission),
  ],
}

export function hasPermission(role: UserRole, permission: Permission): boolean {
  return ROLE_PERMISSIONS[role]?.includes(permission) ?? false
}

export function hasMinimumRole(userRole: UserRole, requiredRole: UserRole): boolean {
  return ROLE_HIERARCHY[userRole] >= ROLE_HIERARCHY[requiredRole]
}

export function getPermissionsForRole(role: UserRole): Permission[] {
  return ROLE_PERMISSIONS[role] ?? []
}

export function canAccessRoute(role: UserRole, requiredRole: UserRole): boolean {
  return hasMinimumRole(role, requiredRole)
}

