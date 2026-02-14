import { Link } from 'react-router-dom'
import { Menu, LogOut, User, Settings, ChevronDown } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Logo, LogoText } from '@/components/ui/logo'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Badge } from '@/components/ui/badge'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { cn } from '@/lib/utils'

interface HeaderProps {
  onMenuClick?: () => void
  showMenuButton?: boolean
}

export function Header({ onMenuClick, showMenuButton = true }: HeaderProps) {
  const { user, logout, isAuthenticated } = useAuth()
  const { instanceInfo } = useInstance()
  
  const addonName = instanceInfo?.addon_name || 'MediaFusion'
  const brandingSvg = instanceInfo?.branding_svg || null

  const getRoleBadgeVariant = (role: string): "gold" | "default" | "secondary" | "outline" => {
    switch (role) {
      case 'admin':
        return 'gold'
      case 'moderator':
        return 'default'
      case 'paid_user':
        return 'secondary'
      default:
        return 'outline'
    }
  }

  const getUserInitials = () => {
    if (user?.username) {
      return user.username.slice(0, 2).toUpperCase()
    }
    if (user?.email) {
      return user.email.slice(0, 2).toUpperCase()
    }
    // Use addon name initials as fallback
    return addonName.slice(0, 2).toUpperCase()
  }

  return (
    <header className="fixed top-0 left-0 right-0 z-40 h-14">
      {/* Background with subtle border */}
      <div className="absolute inset-0 bg-background/95 backdrop-blur-md border-b border-border/40" />
      
      <div className="relative flex h-full items-center px-4 gap-4">
        {showMenuButton && (
          <Button
            variant="ghost"
            size="icon"
            className="md:hidden"
            onClick={onMenuClick}
          >
            <Menu className="h-5 w-5" />
            <span className="sr-only">Toggle menu</span>
          </Button>
        )}

        {/* Logo - Dynamic theme-aware */}
        <Link to="/" className="flex items-center gap-2.5 group">
          <Logo size="md" />
          <LogoText addonName={addonName} size="md" className="hidden sm:inline-flex" />
          {brandingSvg && (
            <>
              <span className="text-muted-foreground/50 text-sm hidden sm:inline">×</span>
              <img 
                src={brandingSvg} 
                alt="Partner Logo"
                className="h-6 w-auto hidden sm:inline"
              />
            </>
          )}
        </Link>

        <div className="flex-1" />

        {/* Actions */}
        <div className="flex items-center gap-2">
          {/* Theme selector */}
          <ThemeSelector />

          {isAuthenticated && user ? (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button 
                  variant="ghost" 
                  className="flex items-center gap-2 px-2 hover:bg-accent"
                >
                  <Avatar className="h-8 w-8 border border-border">
                    <AvatarFallback className="text-xs bg-muted text-foreground font-medium">
                      {getUserInitials()}
                    </AvatarFallback>
                  </Avatar>
                  <div className="hidden sm:flex flex-col items-start">
                    <span className="text-sm font-medium leading-none">
                      {user.username || user.email.split('@')[0]}
                    </span>
                    <Badge
                      variant={getRoleBadgeVariant(user.role)}
                      className="mt-1 text-[10px] px-1.5 py-0 h-4"
                    >
                      {user.role}
                    </Badge>
                  </div>
                  <ChevronDown className="h-4 w-4 opacity-50 hidden sm:block" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <div className="flex flex-col space-y-1">
                    <p className="text-sm font-medium">
                      {user.username || user.email.split('@')[0]}
                    </p>
                    <p className="text-xs text-muted-foreground">{user.email}</p>
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link to="/dashboard" className="cursor-pointer">
                    <User className="mr-2 h-4 w-4" />
                    Dashboard
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link to="/dashboard/configure" className="cursor-pointer">
                    <Settings className="mr-2 h-4 w-4" />
                    Configure
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onClick={() => logout()}
                  className={cn("cursor-pointer text-destructive focus:text-destructive")}
                >
                  <LogOut className="mr-2 h-4 w-4" />
                  Log out
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          ) : (
            <div className="flex items-center gap-2">
              <Button variant="ghost" asChild>
                <Link to="/login">Log in</Link>
              </Button>
              <Button variant="gold" asChild>
                <Link to="/register">Sign up</Link>
              </Button>
            </div>
          )}
        </div>
      </div>
    </header>
  )
}
