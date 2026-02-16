import { Link, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import {
  Play,
  Zap,
  Shield,
  Cloud,
  Settings,
  ExternalLink,
  UserPlus,
  LogIn,
  Search,
  Filter,
  Globe,
  Download,
  Lock,
  Tv,
  Palette,
  Users,
  Star,
  Layers,
  MonitorPlay,
  Rss,
  Database,
  Smartphone,
  Flag,
  Edit3,
  UserCheck,
  RefreshCw,
  FileText,
  Upload,
  Sparkles,
  Radio,
  HardDrive,
  Send,
  Bot,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Logo, LogoWithText, LogoText, BrandingLogo } from '@/components/ui/logo'
import { useAuth } from '@/contexts/AuthContext'
import { useInstance } from '@/contexts/InstanceContext'
import { getAppConfig } from '@/lib/api'
import { ThemeSelector } from '@/components/ui/theme-selector'
import { Skeleton } from '@/components/ui/skeleton'

// Platform Features - What makes MediaFusion special
const platformFeatures = [
  { icon: Smartphone, title: 'Stremio & Kodi', description: 'Native support for popular media apps' },
  { icon: Globe, title: 'Torznab API', description: 'Use as indexer in *arr apps' },
  {
    icon: HardDrive,
    title: 'Usenet Streams',
    description: 'NZB indexer support with Torbox, SABnzbd, NZBGet, NzbDAV & Easynews',
  },
  { icon: Send, title: 'Telegram Streams', description: 'Scrape streams from Telegram & Stream via MediaFlow Proxy' },
  { icon: Radio, title: 'AceStream Support', description: 'P2P live streaming via MediaFlow Proxy & AceEngine' },
  { icon: Lock, title: 'API Security', description: 'Password protection for private instances' },
  { icon: Shield, title: 'Encrypted Config', description: 'Secure configuration storage' },
  { icon: Users, title: 'Multiple Profiles', description: 'Unlimited profiles with unique configurations' },
  {
    icon: RefreshCw,
    title: 'Watch History & Watchlist Sync',
    description: 'Track watched content across devices, Sync watchlists with Trakt, Simkl & more',
  },
  { icon: Upload, title: 'Community Streams', description: 'Import and share streams with the community' },
  { icon: Star, title: 'Community Ratings', description: 'Vote, rate, and review streams collaboratively' },
  { icon: Flag, title: 'Stream Reporting', description: 'Report broken or incorrect streams' },
  { icon: Edit3, title: 'Stream Editing', description: 'Edit and correct detected stream metadata' },
  { icon: UserCheck, title: 'Moderation Tools', description: 'Moderator roles and content management' },
  { icon: FileText, title: 'Metadata Creator', description: 'Import from IMDB, TMDB, TVDB, MAL & Kitsu' },
  { icon: Filter, title: 'Advanced Filters', description: 'Filter by resolution, quality, language' },
  { icon: MonitorPlay, title: 'Web Player', description: 'Watch directly in browser with MediaFlow Proxy' },
  { icon: Download, title: 'Download Manager', description: 'Download content directly to device' },
  { icon: Palette, title: 'Theme Customization', description: '8 color schemes with light/dark modes' },
]

// Streaming Providers
const streamingProviders = [
  { name: 'Direct Torrent', type: 'Free', icon: '📥' },
  { name: 'StremThru', type: 'Proxy', icon: '🔄' },
  { name: 'PikPak', type: 'Freemium', icon: '🌩️' },
  { name: 'Seedr', type: 'Freemium', icon: '🌱' },
  { name: 'OffCloud', type: 'Freemium', icon: '☁️' },
  { name: 'Torbox', type: 'Premium', icon: '🟩' },
  { name: 'Real-Debrid', type: 'Premium', icon: '💎' },
  { name: 'Debrid-Link', type: 'Premium', icon: '🔗' },
  { name: 'Premiumize', type: 'Premium', icon: '✨' },
  { name: 'AllDebrid', type: 'Premium', icon: '🏠' },
  { name: 'EasyDebrid', type: 'Premium', icon: '📦' },
  { name: 'qBittorrent', type: 'Self-hosted', icon: '🔒' },
]

// Content Sources - What we scrape from
const contentSources = [
  { icon: Search, title: 'Prowlarr / Jackett', description: 'User-provided indexer integration' },
  { icon: Database, title: 'Zilean / YTS / BT4G', description: 'Multiple torrent search sources' },
  { icon: Globe, title: 'Torrentio / MediaFusion', description: 'Scrape streams from other addons' },
  { icon: Layers, title: 'Torznab API', description: 'Scrape streams from custom Torznab endpoints' },
  { icon: HardDrive, title: 'Usenet / Newznab', description: 'NZB indexers via Torbox, SABnzbd, NZBGet, NzbDAV & Easynews' },
  { icon: Radio, title: 'AceStream', description: 'P2P live streams via content ID or info hash' },
  { icon: Bot, title: 'Telegram Bot', description: 'Scrape streams from Telegram channels via bot integration' },
  { icon: Rss, title: 'RSS Feeds', description: 'Custom RSS monitoring with regex' },
  {
    icon: Sparkles,
    title: 'Scrapy Spiders',
    description: 'Scrape streams from custom scrapy spiders like TamilMV, SportsVideo etc.',
  },
  { icon: Tv, title: 'IPTV / M3U / Xtream', description: 'Live TV import from multiple formats' },
]

export function HomePage() {
  const { isAuthenticated, isLoading: authLoading } = useAuth()
  const { instanceInfo, isLoading: instanceLoading } = useInstance()

  // Fetch full app config for branding_description
  const { data: appConfig } = useQuery({
    queryKey: ['appConfig'],
    queryFn: getAppConfig,
    staleTime: 5 * 60 * 1000, // 5 minutes
  })

  const addonName = instanceInfo?.addon_name || 'MediaFusion'
  const version = instanceInfo?.version || ''
  const brandingSvg = instanceInfo?.branding_svg || null
  const brandingDescription = appConfig?.branding_description || ''

  // Show loading while checking auth
  if (authLoading || instanceLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center">
        <div className="space-y-4 w-full max-w-md p-8">
          <Skeleton className="h-16 w-16 mx-auto rounded-full" />
          <Skeleton className="h-8 w-48 mx-auto" />
          <Skeleton className="h-4 w-64 mx-auto" />
        </div>
      </div>
    )
  }

  // Redirect authenticated users to dashboard
  if (isAuthenticated) {
    return <Navigate to="/dashboard" replace />
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-50 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="container mx-auto px-4 flex h-16 items-center justify-between">
          <Link to="/app/" className="hover:opacity-80 transition-opacity">
            <LogoWithText size="lg" addonName={addonName} brandingSvg={brandingSvg} />
          </Link>

          <div className="flex items-center gap-3">
            <ThemeSelector />
            <Button asChild variant="ghost">
              <Link to="/login">
                <LogIn className="mr-2 h-4 w-4" />
                Login
              </Link>
            </Button>
            <Button asChild variant="gold">
              <Link to="/register">
                <UserPlus className="mr-2 h-4 w-4" />
                Register
              </Link>
            </Button>
          </div>
        </div>
      </header>

      {/* Hero Section */}
      <section className="relative overflow-hidden border-b">
        <div className="absolute inset-0 hero-gradient" />
        <div className="absolute inset-0 spotlight opacity-50" />
        <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-background" />

        <div className="container mx-auto px-4 relative py-20 md:py-28">
          <div className="max-w-4xl mx-auto text-center space-y-8">
            <div className="flex justify-center items-center gap-6">
              <Logo size="xl" className="w-24 h-24 md:w-32 md:h-32" heroAnimation="spin" />
              {brandingSvg && (
                <>
                  <span className="text-muted-foreground/50 text-4xl font-light">×</span>
                  <BrandingLogo svgUrl={brandingSvg} size="xl" className="h-20 md:h-28" />
                </>
              )}
            </div>

            <div className="space-y-4">
              {version && (
                <Badge variant="secondary" className="text-sm px-4 py-1">
                  v{version}
                </Badge>
              )}
              <h1>
                <LogoText addonName={addonName} size="5xl" />
              </h1>
              <p className="text-xl text-muted-foreground max-w-2xl mx-auto leading-relaxed">
                The ultimate open-source streaming platform. Aggregate content from multiple sources, stream via debrid
                services, and enjoy on Stremio, Kodi, or directly in your browser.
              </p>
            </div>

            {/* Custom branding description (supports HTML) */}
            {brandingDescription && (
              <div
                className="prose prose-sm dark:prose-invert max-w-2xl mx-auto text-muted-foreground [&_a]:text-primary [&_a]:underline [&_a:hover]:text-primary/80 [&_h4]:text-foreground [&_h4]:font-semibold [&_h4]:text-lg [&_p]:my-2"
                dangerouslySetInnerHTML={{ __html: brandingDescription }}
              />
            )}

            <div className="flex flex-wrap justify-center gap-4 pt-4">
              <Button asChild size="lg" variant="gold" className="text-lg px-8">
                <Link to="/configure">
                  <Settings className="mr-2 h-5 w-5" />
                  Configure Add-on
                </Link>
              </Button>
              <Button asChild size="lg" variant="outline" className="text-lg px-8">
                <Link to="/register">
                  <UserPlus className="mr-2 h-5 w-5" />
                  Create Account
                </Link>
              </Button>
            </div>

            <p className="text-sm text-muted-foreground">
              No account required to configure • Create an account to save multiple profiles
            </p>
          </div>
        </div>
      </section>

      {/* Platform Features - FIRST */}
      <section className="py-20 md:py-28">
        <div className="container mx-auto px-4">
          <div className="text-center mb-14">
            <h2 className="font-display text-3xl md:text-4xl font-bold mb-4 flex items-center justify-center gap-3">
              <Zap className="h-8 w-8 text-primary" />
              Platform Features
            </h2>
            <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
              A modern streaming experience with powerful community-driven features
            </p>
          </div>

          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 max-w-7xl mx-auto">
            {platformFeatures.map((feature) => (
              <Card
                key={feature.title}
                className="hover:border-primary/30 transition-all hover:-translate-y-1 text-center"
              >
                <CardContent className="p-5">
                  <div className="w-12 h-12 rounded-lg bg-primary/10 flex items-center justify-center mx-auto mb-3">
                    <feature.icon className="h-6 w-6 text-primary" />
                  </div>
                  <h3 className="font-semibold mb-1.5">{feature.title}</h3>
                  <p className="text-sm text-muted-foreground">{feature.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* Streaming Providers - SECOND */}
      <section className="py-20 md:py-28 bg-muted/30">
        <div className="container mx-auto px-4">
          <div className="text-center mb-14">
            <h2 className="font-display text-3xl md:text-4xl font-bold mb-4 flex items-center justify-center gap-3">
              <Cloud className="h-8 w-8 text-primary" />
              Streaming Providers
            </h2>
            <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
              Stream through your favorite debrid services or directly via torrent
            </p>
          </div>

          <div className="flex flex-wrap justify-center gap-3 max-w-4xl mx-auto">
            {streamingProviders.map((provider) => (
              <Badge
                key={provider.name}
                variant={
                  provider.type === 'Premium'
                    ? 'default'
                    : provider.type === 'Freemium'
                      ? 'secondary'
                      : provider.type === 'Proxy'
                        ? 'outline'
                        : 'outline'
                }
                className="text-sm py-2.5 px-4 gap-2"
              >
                <span>{provider.icon}</span>
                <span>{provider.name}</span>
                <span className="text-xs opacity-60">({provider.type})</span>
              </Badge>
            ))}
          </div>
        </div>
      </section>

      {/* Content Sources - THIRD */}
      <section className="py-20 md:py-28">
        <div className="container mx-auto px-4">
          <div className="text-center mb-14">
            <h2 className="font-display text-3xl md:text-4xl font-bold mb-4 flex items-center justify-center gap-3">
              <Search className="h-8 w-8 text-primary" />
              Content Sources
            </h2>
            <p className="text-muted-foreground text-lg max-w-2xl mx-auto">
              Aggregate streams from multiple sources into a unified experience
            </p>
          </div>

          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3 max-w-5xl mx-auto">
            {contentSources.map((source) => (
              <Card
                key={source.title}
                className="hover:border-primary/30 transition-all hover:-translate-y-1 text-center"
              >
                <CardContent className="p-6">
                  <div className="w-14 h-14 rounded-xl bg-primary/10 flex items-center justify-center mx-auto mb-4">
                    <source.icon className="h-7 w-7 text-primary" />
                  </div>
                  <h3 className="font-semibold text-lg mb-2">{source.title}</h3>
                  <p className="text-sm text-muted-foreground">{source.description}</p>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-20 md:py-28">
        <div className="container mx-auto px-4">
          <Card className="relative overflow-hidden border-primary/20 max-w-4xl mx-auto">
            <div className="absolute inset-0 hero-gradient opacity-50" />
            <CardContent className="relative p-10 md:p-14 text-center">
              <Play className="h-14 w-14 mx-auto text-primary mb-6" />
              <h2 className="font-display text-2xl md:text-3xl font-bold mb-4">Ready to Start Streaming?</h2>
              <p className="text-muted-foreground mb-8 max-w-xl mx-auto text-lg">
                Configure your addon in minutes. No account required for basic setup, or create an account to unlock all
                features.
              </p>
              <div className="flex flex-wrap justify-center gap-4">
                <Button asChild size="lg" variant="gold">
                  <Link to="/configure">
                    <Settings className="mr-2 h-5 w-5" />
                    Start Configuration
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <Link to="/register">
                    <UserPlus className="mr-2 h-5 w-5" />
                    Create Free Account
                  </Link>
                </Button>
              </div>
            </CardContent>
          </Card>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t py-10">
        <div className="container mx-auto px-4">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <Logo size="sm" />
              <span className="text-sm text-muted-foreground">
                {addonName} {version && `v${version}`}
              </span>
            </div>
            <div className="flex items-center gap-6">
              <a
                href="https://github.com/mhdzumair/MediaFusion"
                target="_blank"
                rel="noopener noreferrer"
                className="text-sm text-muted-foreground hover:text-primary transition-colors flex items-center gap-1.5"
              >
                GitHub
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>
          </div>
        </div>
      </footer>
    </div>
  )
}
