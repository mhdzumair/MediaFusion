import { Link } from 'react-router-dom'
import { LogIn, UserPlus, LayoutDashboard } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { LogoWithText } from '@/components/ui/logo'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { ThemeSelector } from '@/components/ui/theme-selector'

interface PublicLayoutProps {
  children: React.ReactNode
}

/**
 * PublicLayout - Layout for public pages (configure, integrations)
 * Shows a simple header with logo and auth buttons
 */
export function PublicLayout({ children }: PublicLayoutProps) {
  const { isAuthenticated } = useAuth()
  const { instanceInfo } = useInstance()
  
  const addonName = instanceInfo?.addon_name || 'MediaFusion'
  const brandingSvg = instanceInfo?.branding_svg || null

  return (
    <div className="relative min-h-screen overflow-hidden">
      {/* Gradient background */}
      <div className="fixed inset-0 -z-10">
        {/* Base gradient */}
        <div className="absolute inset-0 bg-gradient-to-br from-background via-background to-background" />
        
        {/* Animated gradient orbs */}
        <div className="gradient-orb top-0 left-1/4 w-96 h-96 bg-primary dark:bg-primary" />
        <div className="gradient-orb top-1/3 right-0 w-[500px] h-[500px] bg-primary dark:bg-primary animate-delay-1000" />
        <div className="gradient-orb bottom-0 left-1/2 w-80 h-80 bg-primary/70 dark:bg-primary/60 animate-delay-500" />
        
        {/* Grid pattern overlay */}
        <div 
          className="absolute inset-0 opacity-[0.02] dark:opacity-[0.03]"
          style={{
            backgroundImage: `linear-gradient(hsl(var(--foreground) / 0.1) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground) / 0.1) 1px, transparent 1px)`,
            backgroundSize: '60px 60px'
          }}
        />
      </div>

      {/* Header */}
      <header className="sticky top-0 z-50 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container flex h-14 items-center justify-between">
          <Link to="/" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
            <LogoWithText size="md" addonName={addonName} brandingSvg={brandingSvg} />
          </Link>
          
          <div className="flex items-center gap-2">
            <ThemeSelector />
            
            {isAuthenticated ? (
              <Button asChild variant="gold" size="sm">
                <Link to="/dashboard">
                  <LayoutDashboard className="mr-2 h-4 w-4" />
                  Dashboard
                </Link>
              </Button>
            ) : (
              <>
                <Button asChild variant="ghost" size="sm">
                  <Link to="/login">
                    <LogIn className="mr-2 h-4 w-4" />
                    Login
                  </Link>
                </Button>
                <Button asChild variant="gold" size="sm">
                  <Link to="/register">
                    <UserPlus className="mr-2 h-4 w-4" />
                    Register
                  </Link>
                </Button>
              </>
            )}
          </div>
        </div>
      </header>
      
      {/* Content */}
      <main className="pt-4">
        <div className="container mx-auto p-4 md:p-6 lg:p-8">
          {children}
        </div>
      </main>
    </div>
  )
}
