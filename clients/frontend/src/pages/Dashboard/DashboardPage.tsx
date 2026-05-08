import { Link } from 'react-router-dom'
import {
  Settings,
  Library,
  FileInput,
  Rss,
  GitPullRequest,
  ArrowRight,
  Play,
  Bookmark,
  Clock,
  Star,
  Film,
  Clapperboard,
} from 'lucide-react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { useAuth } from '@/contexts/AuthContext'
import { useContinueWatching, useContributionStats, useLibraryStats } from '@/hooks'
import { useRpdb } from '@/contexts/RpdbContext'
import { Poster } from '@/components/ui/poster'
import type { ContinueWatchingItem } from '@/lib/api'

const quickActions = [
  {
    title: 'Configure',
    description: 'Streaming providers & preferences',
    href: '/dashboard/configure',
    icon: Settings,
  },
  {
    title: 'Library',
    description: 'Browse & manage your content',
    href: '/dashboard/library',
    icon: Library,
  },
  {
    title: 'Content Import',
    description: 'Import torrents or M3U playlists',
    href: '/dashboard/content-import',
    icon: FileInput,
  },
  {
    title: 'RSS Feeds',
    description: 'Manage feed subscriptions',
    href: '/dashboard/rss',
    icon: Rss,
  },
  {
    title: 'Contributions',
    description: 'Track your submissions',
    href: '/dashboard/contributions',
    icon: GitPullRequest,
  },
]

export function DashboardPage() {
  const { user } = useAuth()
  const { rpdbApiKey } = useRpdb()

  // Fetch real stats from API
  const { data: libraryStats, isLoading: libraryLoading } = useLibraryStats()
  const { data: continueWatching, isLoading: watchingLoading } = useContinueWatching(undefined, 5)
  const { data: contributionStats, isLoading: contributionsLoading } = useContributionStats()

  const isStatsLoading = libraryLoading || watchingLoading || contributionsLoading

  const stats = [
    {
      label: 'Continue Watching',
      value: continueWatching?.length.toString() ?? '0',
      icon: Clock,
      href: '/dashboard/library',
    },
    {
      label: 'My Library',
      value: libraryStats?.total_items.toString() ?? '0',
      icon: Bookmark,
      href: '/dashboard/library',
    },
    {
      label: 'Contributions',
      value: contributionStats?.total_contributions.toString() ?? '0',
      icon: Star,
      href: '/dashboard/contributions',
    },
    {
      label: 'Saved Movies',
      value: libraryStats?.movies.toString() ?? '0',
      icon: Library,
      href: '/dashboard/library',
    },
  ]

  const getRoleBadgeVariant = (role?: string): 'gold' | 'default' | 'secondary' | 'muted' => {
    switch (role) {
      case 'admin':
        return 'gold'
      case 'moderator':
        return 'default'
      case 'paid_user':
        return 'secondary'
      default:
        return 'muted'
    }
  }

  const getContinueWatchingUrl = (item: ContinueWatchingItem) => {
    if (item.media_type === 'series' && item.season && item.episode) {
      return `/dashboard/content/${item.media_type}/${item.media_id}?season=${item.season}&episode=${item.episode}`
    }

    return `/dashboard/content/${item.media_type}/${item.media_id}`
  }

  return (
    <div className="space-y-8">
      {/* Hero Section - Cinematic Style */}
      <div className="relative overflow-hidden rounded-lg border border-border/40 animate-fade-in">
        {/* Cinematic background with spotlight */}
        <div className="absolute inset-0 hero-gradient" />
        <div className="absolute inset-0 spotlight" />

        {/* Subtle vignette */}
        <div className="absolute inset-0 bg-gradient-to-t from-background/80 via-transparent to-transparent" />

        <div className="relative px-6 py-10 md:px-8 md:py-14">
          <div className="flex items-start justify-between">
            <div className="space-y-4 max-w-xl">
              <div className="flex items-center gap-2">
                <Film className="h-4 w-4 text-primary" />
                <span className="text-sm font-medium text-primary">Welcome back</span>
              </div>
              <h1 className="font-display text-3xl md:text-4xl font-semibold tracking-tight">
                {user?.username ? (
                  <>
                    Hey, <span className="gradient-text">{user.username}</span>!
                  </>
                ) : (
                  <>Hey there!</>
                )}
              </h1>
              <p className="text-muted-foreground">
                Manage your streaming experience, track your activity, and explore new content.
              </p>
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <Button variant="gold" asChild>
                  <Link to="/dashboard/configure">
                    <Settings className="mr-2 h-4 w-4" />
                    Configure Profile
                  </Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link to="/dashboard/content-import">
                    <FileInput className="mr-2 h-4 w-4" />
                    Import Content
                  </Link>
                </Button>
              </div>
            </div>

            {/* Decorative icon */}
            <div className="hidden md:flex items-center justify-center w-28 h-28 rounded-lg bg-primary/10 border border-primary/20">
              <Clapperboard className="h-10 w-10 text-primary" />
            </div>
          </div>
        </div>
      </div>

      {/* Stats Grid */}
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {stats.map((stat, index) => (
          <Link
            key={stat.label}
            to={stat.href}
            className="animate-fade-in"
            style={{ animationDelay: `${(index + 1) * 75}ms` }}
          >
            <Card className="hover:border-primary/30 hover-lift">
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <div className="p-2 rounded-md bg-primary/10">
                      <stat.icon className="h-4 w-4 text-primary" />
                    </div>
                    <div>
                      {isStatsLoading ? (
                        <Skeleton className="h-7 w-12" />
                      ) : (
                        <p className="text-2xl font-display font-semibold">{stat.value}</p>
                      )}
                      <p className="text-xs text-muted-foreground">{stat.label}</p>
                    </div>
                  </div>
                  <ArrowRight className="h-4 w-4 text-muted-foreground" />
                </div>
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>

      {/* Continue Watching Section */}
      {continueWatching && continueWatching.length > 0 && (
        <div className="space-y-4 animate-fade-in animate-delay-300">
          <div className="flex items-center justify-between">
            <h2 className="font-display text-xl font-semibold flex items-center gap-2">
              <Play className="h-5 w-5 text-primary" />
              Continue Watching
            </h2>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/dashboard/library">
                View All
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
            {continueWatching.slice(0, 5).map((item, index) => (
              <Link
                key={item.id}
                to={getContinueWatchingUrl(item)}
                className="group block animate-fade-in"
                style={{ animationDelay: `${(index + 1) * 100}ms` }}
              >
                <Card className="overflow-hidden hover-lift h-full">
                  <div className="aspect-[2/3] relative bg-muted">
                    <Poster
                      metaId={item.external_ids?.imdb || `mf:${item.media_id}`}
                      catalogType={item.media_type === 'tv' ? 'tv' : item.media_type === 'movie' ? 'movie' : 'series'}
                      poster={item.poster}
                      rpdbApiKey={rpdbApiKey}
                      title={item.title}
                      className="absolute inset-0 w-full h-full transition-transform duration-500 group-hover:scale-105"
                    />
                    <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/20 to-transparent" />
                    <div className="absolute bottom-0 left-0 right-0 p-3">
                      <p className="text-white text-sm font-medium line-clamp-2">{item.title}</p>
                      {item.season && item.episode && (
                        <p className="text-white/60 text-xs mt-0.5">
                          S{item.season} E{item.episode}
                        </p>
                      )}
                      <div className="mt-2 h-1 bg-white/20 rounded-full overflow-hidden">
                        <div
                          className="h-full bg-primary transition-all duration-300"
                          style={{ width: `${item.progress_percent}%` }}
                        />
                      </div>
                    </div>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Account Overview */}
      <Card className="animate-fade-in animate-delay-400">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div>
              <CardTitle className="flex items-center gap-2">
                Account Overview
                <Badge variant={getRoleBadgeVariant(user?.role)} className="ml-2">
                  {user?.role}
                </Badge>
              </CardTitle>
              <CardDescription>Your account details and verification status</CardDescription>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Email</p>
              <p className="text-sm font-medium truncate">{user?.email}</p>
            </div>
            {user?.username && (
              <div className="space-y-1">
                <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Username</p>
                <p className="text-sm font-medium">{user.username}</p>
              </div>
            )}
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Status</p>
              <Badge variant={user?.is_verified ? 'success' : 'muted'} className="text-xs">
                {user?.is_verified ? '✓ Verified' : 'Unverified'}
              </Badge>
            </div>
            <div className="space-y-1">
              <p className="text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">Member Since</p>
              <p className="text-sm font-medium">
                {user?.created_at
                  ? new Date(user.created_at).toLocaleDateString('en-US', {
                      month: 'short',
                      day: 'numeric',
                      year: 'numeric',
                    })
                  : 'N/A'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Quick Actions */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="font-display text-xl font-semibold">Quick Actions</h2>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {quickActions.map((action, index) => (
            <Link
              key={action.href}
              to={action.href}
              className="group animate-fade-in"
              style={{ animationDelay: `${(index + 1) * 75}ms` }}
            >
              <Card className="h-full hover:border-primary/30 hover-lift">
                <CardContent className="p-5">
                  <div className="flex items-start justify-between">
                    <div className="p-2.5 rounded-md bg-primary/10 border border-primary/20">
                      <action.icon className="h-5 w-5 text-primary" />
                    </div>
                    <ArrowRight className="h-4 w-4 text-muted-foreground opacity-0 -translate-x-2 group-hover:opacity-100 group-hover:translate-x-0 transition-all duration-300" />
                  </div>
                  <div className="mt-4 space-y-1">
                    <h3 className="font-semibold group-hover:text-primary transition-colors">{action.title}</h3>
                    <p className="text-sm text-muted-foreground">{action.description}</p>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>

      {/* Contribution Stats (if user has contributions) */}
      {contributionStats && contributionStats.total_contributions > 0 && (
        <Card className="animate-fade-in animate-delay-500">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <GitPullRequest className="h-5 w-5 text-primary" />
              Your Contributions
            </CardTitle>
            <CardDescription>Track the status of your submitted metadata and torrents</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-4 sm:grid-cols-4">
              <div className="text-center p-4 rounded-md bg-muted/50 border border-border/50">
                <p className="text-2xl font-display font-semibold">{contributionStats.total_contributions}</p>
                <p className="text-xs text-muted-foreground mt-1">Total</p>
              </div>
              <div className="text-center p-4 rounded-md bg-primary/10 border border-primary/20">
                <p className="text-2xl font-display font-semibold text-primary dark:text-primary">
                  {contributionStats.pending}
                </p>
                <p className="text-xs text-muted-foreground mt-1">Pending</p>
              </div>
              <div className="text-center p-4 rounded-md bg-emerald-500/10 border border-emerald-500/20">
                <p className="text-2xl font-display font-semibold text-emerald-600 dark:text-emerald-400">
                  {contributionStats.approved}
                </p>
                <p className="text-xs text-muted-foreground mt-1">Approved</p>
              </div>
              <div className="text-center p-4 rounded-md bg-red-500/10 border border-red-500/20">
                <p className="text-2xl font-display font-semibold text-red-600 dark:text-red-400">
                  {contributionStats.rejected}
                </p>
                <p className="text-xs text-muted-foreground mt-1">Rejected</p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Get Started CTA */}
      <Card className="relative overflow-hidden border-primary/20 animate-fade-in animate-delay-500">
        <div className="absolute inset-0 hero-gradient opacity-50" />
        <CardContent className="relative p-6">
          <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
            <div className="space-y-2">
              <h3 className="font-display text-lg font-semibold flex items-center gap-2">
                <Film className="h-5 w-5 text-primary" />
                New to MediaFusion?
              </h3>
              <p className="text-sm text-muted-foreground max-w-md">
                Start by configuring your streaming provider to unlock all features and get the best streaming
                experience.
              </p>
            </div>
            <Button variant="gold" asChild className="whitespace-nowrap">
              <Link to="/dashboard/configure">
                Get Started
                <ArrowRight className="ml-2 h-4 w-4" />
              </Link>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
