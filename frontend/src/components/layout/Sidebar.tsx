import { Link, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  Settings,
  Library,
  Rss,
  FileInput,
  Database,
  Shield,
  BarChart3,
  Users,
  GitPullRequest,
  X,
  Link as LinkIcon,
  Calendar,
  HardDrive,
  Radio,
  FilePlus2,
  UserCog,
  Bug,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo } from '@/components/ui/logo'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { cn } from '@/lib/utils'
import type { UserRole } from '@/types'

interface SidebarProps {
  open?: boolean
  onClose?: () => void
}

interface NavItem {
  title: string
  href: string
  icon: React.ElementType
  requiredRole?: UserRole
}

const userNavItems: NavItem[] = [
  { title: 'Dashboard', href: '/dashboard', icon: LayoutDashboard },
  { title: 'Configure', href: '/dashboard/configure', icon: Settings },
  { title: 'Integrations', href: '/dashboard/integrations', icon: LinkIcon },
  { title: 'Library', href: '/dashboard/library', icon: Library },
  { title: 'Content Import', href: '/dashboard/content-import', icon: FileInput },
  { title: 'Metadata Creator', href: '/dashboard/metadata-creator', icon: FilePlus2 },
  { title: 'IPTV Sources', href: '/dashboard/iptv-sources', icon: Radio },
  { title: 'Contributions', href: '/dashboard/contributions', icon: GitPullRequest },
  { title: 'RSS Manager', href: '/dashboard/rss', icon: Rss },
  { title: 'Account Settings', href: '/dashboard/settings', icon: UserCog },
]

const modNavItems: NavItem[] = [
  { title: 'Moderator', href: '/dashboard/moderator', icon: Shield, requiredRole: 'moderator' },
]

const adminNavItems: NavItem[] = [
  { title: 'Metrics', href: '/dashboard/metrics', icon: BarChart3, requiredRole: 'admin' },
  { title: 'Users', href: '/dashboard/users', icon: Users, requiredRole: 'admin' },
  { title: 'Database', href: '/dashboard/database', icon: Database, requiredRole: 'admin' },
  { title: 'Scheduler', href: '/dashboard/scheduler', icon: Calendar, requiredRole: 'admin' },
  { title: 'Cache Manager', href: '/dashboard/cache', icon: HardDrive, requiredRole: 'admin' },
  { title: 'Exceptions', href: '/dashboard/exceptions', icon: Bug, requiredRole: 'admin' },
]

export function Sidebar({ open, onClose }: SidebarProps) {
  const location = useLocation()
  const { hasMinimumRole } = useAuth()
  const { instanceInfo } = useInstance()

  const isActive = (href: string) => {
    if (href === '/dashboard') {
      return location.pathname === '/dashboard'
    }
    return location.pathname.startsWith(href)
  }

  const filterItemsByRole = (items: NavItem[]) => {
    return items.filter((item) => {
      if (!item.requiredRole) return true
      return hasMinimumRole(item.requiredRole)
    })
  }

  const NavLink = ({ item }: { item: NavItem }) => (
    <Link
      to={item.href}
      onClick={onClose}
      className={cn(
        'group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-all duration-200',
        isActive(item.href)
          ? 'bg-primary/10 text-primary border-l-2 border-primary ml-[-1px]'
          : 'text-muted-foreground hover:bg-accent hover:text-foreground',
      )}
    >
      <item.icon
        className={cn('h-4 w-4 transition-colors', isActive(item.href) ? 'text-primary' : 'group-hover:text-primary')}
      />
      {item.title}
    </Link>
  )

  const SectionLabel = ({ children }: { children: React.ReactNode }) => (
    <div className="flex items-center gap-2 px-3 py-2 mt-6 mb-2">
      <div className="h-px flex-1 bg-border/50" />
      <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">{children}</span>
      <div className="h-px flex-1 bg-border/50" />
    </div>
  )

  const sidebarContent = (
    <div className="flex h-full flex-col">
      {/* Mobile header */}
      <div className="flex items-center justify-between p-4 md:hidden border-b border-border/40">
        <div className="flex items-center gap-2">
          <Logo size="md" />
          <span className="font-display font-semibold text-lg">Menu</span>
        </div>
        <Button variant="ghost" size="icon" onClick={onClose}>
          <X className="h-5 w-5" />
        </Button>
      </div>

      <ScrollArea className="flex-1 px-3 py-4">
        <div className="space-y-1">
          {filterItemsByRole(userNavItems).map((item) => (
            <NavLink key={item.href} item={item} />
          ))}
        </div>

        {hasMinimumRole('moderator') && (
          <>
            <SectionLabel>Moderation</SectionLabel>
            <div className="space-y-1">
              {filterItemsByRole(modNavItems).map((item) => (
                <NavLink key={item.href} item={item} />
              ))}
            </div>
          </>
        )}

        {hasMinimumRole('admin') && (
          <>
            <SectionLabel>Administration</SectionLabel>
            <div className="space-y-1">
              {filterItemsByRole(adminNavItems).map((item) => (
                <NavLink key={item.href} item={item} />
              ))}
            </div>
          </>
        )}
      </ScrollArea>

      {/* Footer */}
      <div className="border-t border-border/40 p-4">
        <div className="flex items-center justify-center gap-2 text-xs text-muted-foreground">
          <div className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
          <span>
            {instanceInfo?.addon_name || 'MediaFusion'} v{instanceInfo?.version || '...'}
          </span>
        </div>
      </div>
    </div>
  )

  return (
    <>
      {/* Mobile overlay */}
      {open && <div className="fixed inset-0 z-40 bg-background/80 backdrop-blur-sm md:hidden" onClick={onClose} />}

      {/* Mobile sidebar */}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 w-72 transform transition-transform duration-300 ease-out md:hidden',
          'bg-card border-r border-border/40',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
      >
        {sidebarContent}
      </aside>

      {/* Desktop sidebar */}
      <aside className="hidden md:fixed md:inset-y-0 md:flex md:w-64 md:flex-col md:pt-14 z-30">
        <div className="flex-1 bg-card/50 backdrop-blur-sm border-r border-border/40">{sidebarContent}</div>
      </aside>
    </>
  )
}
