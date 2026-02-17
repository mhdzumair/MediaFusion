import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { TooltipProvider } from '@/components/ui/tooltip'
import { Toaster } from '@/components/ui/toaster'
import { ThemeProvider } from '@/contexts/ThemeContext'
import { AuthProvider } from '@/contexts/AuthContext'
import { InstanceProvider } from '@/contexts/InstanceContext'
import { RpdbProvider } from '@/contexts/RpdbContext'
import { AppShell, PublicLayout } from '@/components/layout'
import { AuthGuard, GuestGuard, RoleGuard, OptionalAuthGuard, SetupGuard } from '@/components/guards'

// Pages
import { HomePage } from '@/pages/Home'
import { SetupWizardPage } from '@/pages/Setup'
import { DashboardPage } from '@/pages/Dashboard'
import { LoginPage, RegisterPage, ExtensionAuthPage, TelegramLoginPage } from '@/pages/Auth'
import { ConfigurePage } from '@/pages/Configure'
import { ContentImportPage } from '@/pages/ContentImport'
import { ContributionsPage } from '@/pages/Contributions'
import { RSSManagerPage } from '@/pages/RSSManager'
import { MetricsPage } from '@/pages/Metrics'
import { UserManagementPage } from '@/pages/UserManagement'
import { LibraryPage } from '@/pages/Library'
import { IPTVSourcesPage } from '@/pages/IPTVSources'
import { ContentDetailPage } from '@/pages/Content'
import { IntegrationsPage } from '@/pages/Integrations'
import { ModeratorDashboardPage } from '@/pages/Moderator'
import { SchedulerPage } from '@/pages/Scheduler'
import { CacheManagerPage } from '@/pages/CacheManager'
import { DatabaseManagerPage } from '@/pages/DatabaseManager'
import { ExceptionTrackerPage } from '@/pages/ExceptionTracker'
import { RequestMetricsPage } from '@/pages/RequestMetrics'
import { MetadataCreatorPage } from '@/pages/MetadataCreator'
import { SettingsPage } from '@/pages/Settings'
import { PrivacyPolicyPage, TermsOfServicePage, DMCAPage } from '@/pages/Legal'

// Create a client
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
})

function AppRoutes() {
  return (
    <Routes>
      {/* Setup wizard - NOT wrapped by SetupGuard (it IS the setup destination) */}
      <Route path="/setup" element={<SetupWizardPage />} />

      {/* Public home page - redirects to dashboard if authenticated */}
      <Route
        index
        element={
          <SetupGuard>
            <HomePage />
          </SetupGuard>
        }
      />

      {/* Public configure page - works both authenticated and anonymous */}
      <Route
        path="/configure"
        element={
          <SetupGuard>
            <OptionalAuthGuard>
              <PublicLayout>
                <ConfigurePage />
              </PublicLayout>
            </OptionalAuthGuard>
          </SetupGuard>
        }
      />

      {/* Guest-only routes (login/register) */}
      <Route
        path="/login"
        element={
          <SetupGuard>
            <GuestGuard>
              <LoginPage />
            </GuestGuard>
          </SetupGuard>
        }
      />
      <Route
        path="/register"
        element={
          <SetupGuard>
            <GuestGuard>
              <RegisterPage />
            </GuestGuard>
          </SetupGuard>
        }
      />

      {/* Extension auth - standalone page for browser extension authorization */}
      <Route
        path="/extension-auth"
        element={
          <SetupGuard>
            <ExtensionAuthPage />
          </SetupGuard>
        }
      />

      {/* Telegram login - requires authentication, redirects to login if not authenticated */}
      <Route
        path="/telegram/login"
        element={
          <SetupGuard>
            <OptionalAuthGuard>
              <TelegramLoginPage />
            </OptionalAuthGuard>
          </SetupGuard>
        }
      />

      {/* Legal pages - publicly accessible */}
      <Route
        path="/privacy"
        element={
          <PublicLayout>
            <PrivacyPolicyPage />
          </PublicLayout>
        }
      />
      <Route
        path="/terms"
        element={
          <PublicLayout>
            <TermsOfServicePage />
          </PublicLayout>
        }
      />
      <Route
        path="/dmca"
        element={
          <PublicLayout>
            <DMCAPage />
          </PublicLayout>
        }
      />

      {/* Authenticated routes within AppShell */}
      <Route
        path="/dashboard"
        element={
          <SetupGuard>
            <AuthGuard>
              <AppShell />
            </AuthGuard>
          </SetupGuard>
        }
      >
        {/* Dashboard index */}
        <Route index element={<DashboardPage />} />

        {/* User routes - nested under /dashboard */}
        <Route path="configure" element={<ConfigurePage />} />
        <Route path="configure/:profileId" element={<ConfigurePage />} />
        <Route path="library" element={<LibraryPage />} />
        <Route path="iptv-sources" element={<IPTVSourcesPage />} />
        <Route path="content/:type/:id" element={<ContentDetailPage />} />
        <Route path="content-import" element={<ContentImportPage />} />
        <Route path="import" element={<ContentImportPage />} />
        <Route path="contributions" element={<ContributionsPage />} />
        <Route path="integrations" element={<IntegrationsPage />} />
        <Route path="metadata-creator" element={<MetadataCreatorPage />} />
        <Route path="rss" element={<RSSManagerPage />} />
        <Route path="settings" element={<SettingsPage />} />

        {/* Moderator routes */}
        <Route
          path="moderator"
          element={
            <RoleGuard requiredRole="moderator">
              <ModeratorDashboardPage />
            </RoleGuard>
          }
        />

        {/* Admin routes */}
        <Route
          path="metrics"
          element={
            <RoleGuard requiredRole="admin">
              <MetricsPage />
            </RoleGuard>
          }
        />
        <Route
          path="database"
          element={
            <RoleGuard requiredRole="admin">
              <DatabaseManagerPage />
            </RoleGuard>
          }
        />
        <Route
          path="users"
          element={
            <RoleGuard requiredRole="admin">
              <UserManagementPage />
            </RoleGuard>
          }
        />
        <Route
          path="rss/admin"
          element={
            <RoleGuard requiredRole="admin">
              <RSSManagerPage />
            </RoleGuard>
          }
        />
        <Route
          path="scheduler"
          element={
            <RoleGuard requiredRole="admin">
              <SchedulerPage />
            </RoleGuard>
          }
        />
        <Route
          path="cache"
          element={
            <RoleGuard requiredRole="admin">
              <CacheManagerPage />
            </RoleGuard>
          }
        />
        <Route
          path="exceptions"
          element={
            <RoleGuard requiredRole="admin">
              <ExceptionTrackerPage />
            </RoleGuard>
          }
        />
        <Route
          path="request-metrics"
          element={
            <RoleGuard requiredRole="admin">
              <RequestMetricsPage />
            </RoleGuard>
          }
        />
      </Route>

      {/* Catch-all redirect to home */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <BrowserRouter basename="/app">
          <InstanceProvider>
            <AuthProvider>
              <RpdbProvider>
                <TooltipProvider>
                  <AppRoutes />
                  <Toaster />
                </TooltipProvider>
              </RpdbProvider>
            </AuthProvider>
          </InstanceProvider>
        </BrowserRouter>
      </ThemeProvider>
    </QueryClientProvider>
  )
}
