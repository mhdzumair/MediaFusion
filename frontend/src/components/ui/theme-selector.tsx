import { Check, Palette, Moon, Sun, Monitor } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { useTheme, colorSchemes } from '@/contexts/ThemeContext'
import { cn } from '@/lib/utils'

export function ThemeSelector() {
  const { theme, setTheme, colorScheme, setColorScheme } = useTheme()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" className="relative">
          <Palette className="h-4 w-4" />
          <span className="sr-only">Theme settings</span>
          {/* Color indicator dot */}
          <span 
            className="absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-2 border-background"
            style={{ backgroundColor: colorSchemes.find(s => s.id === colorScheme)?.preview.primary }}
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel className="font-display">Appearance</DropdownMenuLabel>
        <DropdownMenuSeparator />
        
        {/* Light/Dark Mode */}
        <div className="p-2">
          <p className="text-xs text-muted-foreground mb-2">Mode</p>
          <div className="flex gap-1">
            <Button
              variant={theme === 'light' ? 'default' : 'outline'}
              size="sm"
              className="flex-1 h-8"
              onClick={() => setTheme('light')}
            >
              <Sun className="h-3.5 w-3.5 mr-1.5" />
              Light
            </Button>
            <Button
              variant={theme === 'dark' ? 'default' : 'outline'}
              size="sm"
              className="flex-1 h-8"
              onClick={() => setTheme('dark')}
            >
              <Moon className="h-3.5 w-3.5 mr-1.5" />
              Dark
            </Button>
            <Button
              variant={theme === 'system' ? 'default' : 'outline'}
              size="sm"
              className="flex-1 h-8"
              onClick={() => setTheme('system')}
            >
              <Monitor className="h-3.5 w-3.5 mr-1.5" />
              Auto
            </Button>
          </div>
        </div>
        
        <DropdownMenuSeparator />
        
        {/* Color Schemes */}
        <div className="p-2">
          <p className="text-xs text-muted-foreground mb-2">Color Scheme</p>
          <div className="space-y-1">
            {colorSchemes.map((scheme) => (
              <button
                key={scheme.id}
                onClick={() => setColorScheme(scheme.id)}
                className={cn(
                  'w-full flex items-center gap-3 p-2 rounded-lg transition-colors text-left',
                  colorScheme === scheme.id
                    ? 'bg-primary/10 text-foreground'
                    : 'hover:bg-muted text-muted-foreground hover:text-foreground'
                )}
              >
                {/* Color preview dots */}
                <div className="flex -space-x-1">
                  <span
                    className="h-4 w-4 rounded-full border-2 border-background shadow-sm"
                    style={{ backgroundColor: scheme.preview.primary }}
                  />
                  <span
                    className="h-4 w-4 rounded-full border-2 border-background shadow-sm"
                    style={{ backgroundColor: scheme.preview.secondary }}
                  />
                  <span
                    className="h-4 w-4 rounded-full border-2 border-background shadow-sm"
                    style={{ backgroundColor: scheme.preview.accent }}
                  />
                </div>
                
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{scheme.name}</p>
                </div>
                
                {colorScheme === scheme.id && (
                  <Check className="h-4 w-4 text-primary flex-shrink-0" />
                )}
              </button>
            ))}
          </div>
        </div>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

// Compact version for mobile or smaller spaces
export function ThemeSelectorCompact() {
  const { colorScheme, setColorScheme } = useTheme()

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-2">
          <span 
            className="h-3 w-3 rounded-full"
            style={{ backgroundColor: colorSchemes.find(s => s.id === colorScheme)?.preview.primary }}
          />
          <span className="text-xs">Theme</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-48">
        {colorSchemes.map((scheme) => (
          <DropdownMenuItem
            key={scheme.id}
            onClick={() => setColorScheme(scheme.id)}
            className="gap-2"
          >
            <span
              className="h-3 w-3 rounded-full"
              style={{ backgroundColor: scheme.preview.primary }}
            />
            <span className="flex-1">{scheme.name}</span>
            {colorScheme === scheme.id && <Check className="h-3.5 w-3.5" />}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
