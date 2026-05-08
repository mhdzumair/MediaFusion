import { useState } from 'react'
import { Outlet } from 'react-router-dom'
import { Header } from './Header'
import { Sidebar } from './Sidebar'

export function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)

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
        <div className="gradient-orb bottom-1/4 right-1/4 w-64 h-64 bg-primary/60 dark:bg-primary/50 animate-delay-700" />

        {/* Grid pattern overlay */}
        <div
          className="absolute inset-0 opacity-[0.02] dark:opacity-[0.03]"
          style={{
            backgroundImage: `linear-gradient(hsl(var(--foreground) / 0.1) 1px, transparent 1px), linear-gradient(90deg, hsl(var(--foreground) / 0.1) 1px, transparent 1px)`,
            backgroundSize: '60px 60px',
          }}
        />
      </div>

      {/* Content */}
      <Header onMenuClick={() => setSidebarOpen(true)} showMenuButton={true} />
      <Sidebar open={sidebarOpen} onClose={() => setSidebarOpen(false)} />

      <main className="md:pl-64 pt-14">
        <div className="container mx-auto p-4 md:p-6 lg:p-8">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
