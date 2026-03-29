import { Logo } from '@/components/ui/logo'

/**
 * Full-screen loader shown while auth/instance bootstrap is in flight.
 * Matches the animated logo used on the public home page hero.
 */
export function AppLoadingScreen() {
  return (
    <div className="min-h-screen bg-background flex items-center justify-center">
      <Logo size="xl" className="w-24 h-24 md:w-32 md:h-32" heroAnimation="spin" />
    </div>
  )
}
