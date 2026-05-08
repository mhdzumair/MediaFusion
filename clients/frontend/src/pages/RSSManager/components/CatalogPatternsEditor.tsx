import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Badge } from '@/components/ui/badge'
import { Checkbox } from '@/components/ui/checkbox'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Plus, Trash2, ChevronDown, TestTube2, AlertCircle, Check } from 'lucide-react'
import type { CatalogPattern } from '@/lib/api'
import { CATALOG_OPTIONS } from './constants'

interface CatalogPatternsEditorProps {
  patterns: CatalogPattern[]
  onChange: (patterns: CatalogPattern[]) => void
  sampleData?: Record<string, unknown>
}

export function CatalogPatternsEditor({ patterns, onChange, sampleData }: CatalogPatternsEditorProps) {
  const [testResults, setTestResults] = useState<Record<number, { match: boolean; value?: string }>>({})

  const addPattern = () => {
    const newPattern: CatalogPattern = {
      name: `Pattern ${patterns.length + 1}`,
      regex: '',
      enabled: true,
      case_sensitive: false,
      target_catalogs: [],
    }
    onChange([...patterns, newPattern])
  }

  const removePattern = (index: number) => {
    const updated = patterns.filter((_, i) => i !== index)
    onChange(updated)
    // Clean up test results
    const newResults = { ...testResults }
    delete newResults[index]
    setTestResults(newResults)
  }

  const updatePattern = (index: number, updates: Partial<CatalogPattern>) => {
    const updated = patterns.map((p, i) => (i === index ? { ...p, ...updates } : p))
    onChange(updated)
  }

  const toggleCatalog = (patternIndex: number, catalogId: string) => {
    const pattern = patterns[patternIndex]
    const catalogs = pattern.target_catalogs || []
    const updated = catalogs.includes(catalogId) ? catalogs.filter((c) => c !== catalogId) : [...catalogs, catalogId]
    updatePattern(patternIndex, { target_catalogs: updated })
  }

  const testPattern = (index: number) => {
    const pattern = patterns[index]
    if (!pattern.regex || !sampleData) {
      setTestResults((prev) => ({ ...prev, [index]: { match: false } }))
      return
    }

    try {
      const regex = new RegExp(pattern.regex, pattern.case_sensitive ? 'g' : 'gi')

      // Try to find a match in common title fields
      const testFields = ['title', 'name', 'description']
      for (const field of testFields) {
        const value = sampleData[field]
        if (typeof value === 'string') {
          const match = regex.test(value)
          if (match) {
            const execResult = regex.exec(value)
            setTestResults((prev) => ({
              ...prev,
              [index]: {
                match: true,
                value: execResult ? execResult[0] : value,
              },
            }))
            return
          }
        }
      }

      setTestResults((prev) => ({ ...prev, [index]: { match: false } }))
    } catch {
      setTestResults((prev) => ({ ...prev, [index]: { match: false } }))
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <Label className="text-sm font-medium">Catalog Detection Patterns</Label>
        <Button variant="outline" size="sm" onClick={addPattern}>
          <Plus className="mr-2 h-3 w-3" />
          Add Pattern
        </Button>
      </div>

      {patterns.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-4">
          No patterns configured. Add a pattern to auto-detect catalogs based on title.
        </p>
      ) : (
        <div className="space-y-3">
          {patterns.map((pattern, index) => (
            <Collapsible key={index} className="border rounded-lg">
              <CollapsibleTrigger className="flex items-center justify-between w-full p-3 hover:bg-muted/50">
                <div className="flex items-center gap-2">
                  <ChevronDown className="h-4 w-4" />
                  <span className="font-medium text-sm">{pattern.name || `Pattern ${index + 1}`}</span>
                  {pattern.enabled ? (
                    <Badge variant="secondary" className="text-xs">
                      Active
                    </Badge>
                  ) : (
                    <Badge variant="outline" className="text-xs">
                      Disabled
                    </Badge>
                  )}
                  {testResults[index] &&
                    (testResults[index].match ? (
                      <Badge variant="default" className="text-xs bg-emerald-500">
                        <Check className="h-3 w-3 mr-1" />
                        Match
                      </Badge>
                    ) : (
                      <Badge variant="destructive" className="text-xs">
                        <AlertCircle className="h-3 w-3 mr-1" />
                        No Match
                      </Badge>
                    ))}
                </div>
                <div className="flex items-center gap-1">
                  <Badge variant="outline" className="text-xs">
                    {(pattern.target_catalogs || []).length} catalogs
                  </Badge>
                </div>
              </CollapsibleTrigger>

              <CollapsibleContent className="px-3 pb-3 space-y-4">
                <div className="grid gap-3 pt-2">
                  <div className="grid grid-cols-2 gap-3">
                    <div className="space-y-1">
                      <Label className="text-xs">Pattern Name</Label>
                      <Input
                        value={pattern.name || ''}
                        onChange={(e) => updatePattern(index, { name: e.target.value })}
                        placeholder="Pattern name"
                        className="text-sm"
                      />
                    </div>

                    <div className="space-y-1">
                      <Label className="text-xs">Regex Pattern</Label>
                      <div className="flex gap-1">
                        <Input
                          value={pattern.regex}
                          onChange={(e) => updatePattern(index, { regex: e.target.value })}
                          placeholder="e.g., (?i)tamil|தமிழ்"
                          className="text-sm font-mono"
                        />
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => testPattern(index)}
                          disabled={!sampleData}
                          title={sampleData ? 'Test pattern' : 'Test feed first'}
                        >
                          <TestTube2 className="h-3 w-3" />
                        </Button>
                      </div>
                    </div>
                  </div>

                  {testResults[index]?.match && testResults[index]?.value && (
                    <div className="text-xs text-emerald-500 bg-emerald-500/10 p-2 rounded">
                      Matched: <code className="font-mono">{testResults[index].value}</code>
                    </div>
                  )}

                  <div className="flex items-center gap-4">
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={pattern.enabled}
                        onCheckedChange={(checked) => updatePattern(index, { enabled: checked })}
                      />
                      <Label className="text-xs">Enabled</Label>
                    </div>

                    <div className="flex items-center gap-2">
                      <Switch
                        checked={pattern.case_sensitive}
                        onCheckedChange={(checked) => updatePattern(index, { case_sensitive: checked })}
                      />
                      <Label className="text-xs">Case Sensitive</Label>
                    </div>

                    <div className="ml-auto">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => removePattern(index)}
                        className="text-red-500 hover:text-red-600 hover:bg-red-500/10"
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>

                  {/* Catalog selection */}
                  <div className="space-y-2">
                    <Label className="text-xs font-medium">Target Catalogs</Label>

                    {/* Movies */}
                    <div className="space-y-1">
                      <span className="text-xs text-muted-foreground">Movies</span>
                      <div className="flex flex-wrap gap-1">
                        {CATALOG_OPTIONS.movies.map((catalog) => (
                          <label
                            key={catalog.id}
                            className={`flex items-center gap-1 px-2 py-1 text-xs rounded cursor-pointer border transition-colors ${
                              (pattern.target_catalogs || []).includes(catalog.id)
                                ? 'bg-primary/20 border-primary text-primary'
                                : 'bg-muted hover:bg-muted/80'
                            }`}
                          >
                            <Checkbox
                              checked={(pattern.target_catalogs || []).includes(catalog.id)}
                              onCheckedChange={() => toggleCatalog(index, catalog.id)}
                              className="h-3 w-3"
                            />
                            {catalog.name}
                          </label>
                        ))}
                      </div>
                    </div>

                    {/* Series */}
                    <div className="space-y-1">
                      <span className="text-xs text-muted-foreground">Series</span>
                      <div className="flex flex-wrap gap-1">
                        {CATALOG_OPTIONS.series.map((catalog) => (
                          <label
                            key={catalog.id}
                            className={`flex items-center gap-1 px-2 py-1 text-xs rounded cursor-pointer border transition-colors ${
                              (pattern.target_catalogs || []).includes(catalog.id)
                                ? 'bg-emerald-500/20 border-emerald-500 text-emerald-500'
                                : 'bg-muted hover:bg-muted/80'
                            }`}
                          >
                            <Checkbox
                              checked={(pattern.target_catalogs || []).includes(catalog.id)}
                              onCheckedChange={() => toggleCatalog(index, catalog.id)}
                              className="h-3 w-3"
                            />
                            {catalog.name}
                          </label>
                        ))}
                      </div>
                    </div>

                    {/* Sports */}
                    <div className="space-y-1">
                      <span className="text-xs text-muted-foreground">Sports</span>
                      <div className="flex flex-wrap gap-1">
                        {CATALOG_OPTIONS.sports.map((catalog) => (
                          <label
                            key={catalog.id}
                            className={`flex items-center gap-1 px-2 py-1 text-xs rounded cursor-pointer border transition-colors ${
                              (pattern.target_catalogs || []).includes(catalog.id)
                                ? 'bg-primary/20 border-primary text-primary'
                                : 'bg-muted hover:bg-muted/80'
                            }`}
                          >
                            <Checkbox
                              checked={(pattern.target_catalogs || []).includes(catalog.id)}
                              onCheckedChange={() => toggleCatalog(index, catalog.id)}
                              className="h-3 w-3"
                            />
                            {catalog.name}
                          </label>
                        ))}
                      </div>
                    </div>
                  </div>
                </div>
              </CollapsibleContent>
            </Collapsible>
          ))}
        </div>
      )}
    </div>
  )
}
