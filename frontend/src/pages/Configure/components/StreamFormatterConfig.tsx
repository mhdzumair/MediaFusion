import { useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Separator } from '@/components/ui/separator'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { Code, Wand2, Eye, RotateCcw, Copy, Check, ArrowRightLeft, Sparkles } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ConfigSectionProps } from './types'

// Default templates using MediaFusion simplified syntax
// Stream type indicators: 🧲 Torrent, 📰 Usenet, 🔗 HTTP/Direct
const DEFAULT_TITLE_TEMPLATE = `{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`
const DEFAULT_DESCRIPTION_TEMPLATE = `{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}
{if stream.size > 0}📦 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}
{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}
🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}`

// Preset templates using new MediaFusion syntax
// Stream type indicators: 🧲 Torrent, 📰 Usenet/NZB, 🔗 HTTP/Direct, 📺 TV
const PRESETS = {
  default: {
    name: 'Default',
    description: 'Standard MediaFusion format with stream type',
    title: DEFAULT_TITLE_TEMPLATE,
    desc: DEFAULT_DESCRIPTION_TEMPLATE,
  },
  torrentio: {
    name: 'Torrentio',
    description: 'Similar to Torrentio addon',
    title: `{if stream.type = torrent}[🧲{service.shortName}{if service.cached}⚡{/if}]{elif stream.type = usenet}[📰{service.shortName}]{else}[🔗]{/if} {addon.name} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `{if stream.quality}{stream.quality} {/if}{if stream.codec}{stream.codec} {/if}{if stream.hdr_formats}{stream.hdr_formats|join(' ')} {/if}
{if stream.size > 0}💾 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}
{if stream.language_flags}{stream.language_flags|join(' ')}{/if}
⚙️ {stream.source}`,
  },
  minimal: {
    name: 'Minimal',
    description: 'Clean and compact display',
    title: `{addon.name} {if stream.type = torrent}🧲{elif stream.type = usenet}📰{else}🔗{/if} {if stream.resolution}{stream.resolution} {/if}{if stream.type = torrent}{if service.cached}⚡️{else}⏳{/if}{/if}`,
    desc: `{if stream.quality}{stream.quality} | {/if}{if stream.codec}{stream.codec} | {/if}{if stream.size > 0}{stream.size|bytes}{/if}`,
  },
  detailed: {
    name: 'Detailed',
    description: 'Maximum information density',
    title: `{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `📂 {stream.name}
{if stream.type = torrent}🧲 Torrent{elif stream.type = usenet}📰 Usenet/NZB{elif stream.type = http}🔗 Direct Stream{else}📺 {stream.type|title}{/if}
{if stream.quality}🎥 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.bit_depth}{stream.bit_depth}-bit {/if}
{if stream.hdr_formats}🎨 {stream.hdr_formats|join(' ')} {/if}{if stream.audio_formats}🎧 {stream.audio_formats|join(' ')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')} {/if}
{if stream.size > 0}📦 {stream.size|bytes} {/if}{if stream.seeders > 0}👤 {stream.seeders} seeders {/if}
{if stream.languages}🌐 {stream.languages|join(' | ')}{/if}
🔗 {stream.source}{if stream.release_group} | 🏷️ {stream.release_group}{/if}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}`,
  },
  usenetFocused: {
    name: 'Usenet Focus',
    description: 'Optimized for Usenet/NZB streams',
    title: `{addon.name} {if stream.type = usenet}📰 NZB{elif stream.type = torrent}🧲 {service.shortName}{if service.cached}⚡{/if}{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}`,
    desc: `{if stream.type = usenet}📰 Usenet • {stream.source}{elif stream.type = torrent}🧲 Torrent • {if service.cached}Cached{else}Not Cached{/if}{else}🔗 Direct{/if}
{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.hdr_formats}🎨 {stream.hdr_formats|join(' ')}{/if}
{if stream.audio_formats}🎵 {stream.audio_formats|join(' ')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}
{if stream.size > 0}📦 {stream.size|bytes}{/if}{if stream.seeders > 0} • 👤 {stream.seeders}{/if}
{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}`,
  },
}

// Available fields organized by category
const FIELD_GROUPS = {
  addon: {
    label: '🏷️ Addon',
    fields: [
      { field: 'addon.name', description: 'Addon name (MediaFusion)' },
    ],
  },
  service: {
    label: '☁️ Debrid Service',
    fields: [
      { field: 'service.name', description: 'Full debrid service name' },
      { field: 'service.shortName', description: 'Short name (RD, AD, TB, etc.)' },
      { field: 'service.cached', description: 'Is stream cached (true/false)' },
    ],
  },
  stream: {
    label: '🎬 Stream Info',
    fields: [
      { field: 'stream.name', description: 'Full torrent/stream name' },
      { field: 'stream.filename', description: 'Video filename being played' },
      { field: 'stream.type', description: 'Stream type (torrent, http, usenet, etc.)' },
      { field: 'stream.resolution', description: 'Resolution (4K, 1080p, 720p)' },
      { field: 'stream.quality', description: 'Quality (WEB-DL, BluRay, HDRip)' },
      { field: 'stream.codec', description: 'Video codec (x265, x264, AV1)' },
      { field: 'stream.bit_depth', description: 'Bit depth (8, 10, 12)' },
      { field: 'stream.size', description: 'File size in bytes (use |bytes)' },
      { field: 'stream.seeders', description: 'Number of seeders (torrent only)' },
      { field: 'stream.cached', description: 'Is cached on debrid' },
    ],
  },
  arrays: {
    label: '📋 Arrays (use |join)',
    fields: [
      { field: 'stream.audio_formats', description: 'Audio formats (DTS-HD, Atmos)' },
      { field: 'stream.channels', description: 'Audio channels (5.1, 7.1)' },
      { field: 'stream.hdr_formats', description: 'HDR formats (HDR10, DV)' },
      { field: 'stream.languages', description: 'Language names (English, Hindi)' },
      { field: 'stream.language_flags', description: 'Country flag emojis (🇬🇧, 🇮🇳)' },
    ],
  },
  metadata: {
    label: '📝 Metadata',
    fields: [
      { field: 'stream.source', description: 'Source/catalog name' },
      { field: 'stream.release_group', description: 'Release group name' },
      { field: 'stream.uploader', description: 'Uploader name' },
    ],
  },
}

// Syntax reference for new MediaFusion format
const SYNTAX_EXAMPLES = [
  { 
    category: 'Variables',
    examples: [
      { code: '{stream.resolution}', desc: 'Simple variable' },
      { code: '{stream.size|bytes}', desc: 'With modifier' },
      { code: '{stream.name|upper|truncate(30)}', desc: 'Chained modifiers' },
    ]
  },
  {
    category: 'Conditionals',
    examples: [
      { code: '{if service.cached}⚡️{/if}', desc: 'Simple if' },
      { code: '{if service.cached}⚡️{else}⏳{/if}', desc: 'If/else' },
      { code: '{if stream.type = torrent}...{elif stream.type = http}...{else}...{/if}', desc: 'If/elif/else' },
    ]
  },
  {
    category: 'Comparisons',
    examples: [
      { code: '{if stream.size > 0}...{/if}', desc: 'Greater than' },
      { code: '{if stream.type = torrent}...{/if}', desc: 'Equality' },
      { code: '{if stream.name ~ 720}...{/if}', desc: 'Contains' },
    ]
  },
  {
    category: 'Logical',
    examples: [
      { code: '{if cached and stream.type = torrent}...{/if}', desc: 'AND' },
      { code: '{if cached or stream.library}...{/if}', desc: 'OR' },
      { code: '{if not stream.cached}...{/if}', desc: 'NOT' },
    ]
  },
]

// Modifiers reference
const MODIFIERS = [
  { modifier: '|bytes', description: 'Format bytes (1.5 GB)' },
  { modifier: '|time', description: 'Format duration (HH:MM:SS)' },
  { modifier: "|join(', ')", description: 'Join array with separator' },
  { modifier: '|upper', description: 'Uppercase' },
  { modifier: '|lower', description: 'Lowercase' },
  { modifier: '|title', description: 'Title case' },
  { modifier: '|first', description: 'First array element' },
  { modifier: '|last', description: 'Last array element' },
  { modifier: '|truncate(50)', description: 'Truncate to N chars' },
  { modifier: '|escape', description: 'HTML escape' },
]

/**
 * Convert AIOStreams syntax to MediaFusion syntax
 * This is a client-side implementation for the converter dialog
 */
function convertAIOStreamsToMediaFusion(template: string): string {
  if (!template) return template
  
  let result = template
  
  // Convert conditionals: {var::check["true"||"false"]} -> {if condition}true{else}false{/if}
  // Pattern matches: {var::modifier["content"||"content"]}
  const conditionalPattern = /\{([a-zA-Z_][a-zA-Z0-9_.]+)::([=<>!~$^]?\w*)\[(['"])(.+?)\3\|\|(['"])(.+?)\5\]\}/g
  
  function convertMatch(_match: string, varPath: string, check: string, _q1: string, trueVal: string, _q2: string, falseVal: string): string {
    // Recursively convert nested AIOStreams syntax
    trueVal = convertAIOStreamsToMediaFusion(trueVal)
    falseVal = convertAIOStreamsToMediaFusion(falseVal)
    
    let condition: string
    if (check === 'istrue' || check === 'exists') {
      condition = varPath
    } else if (check === 'isfalse') {
      condition = `not ${varPath}`
    } else if (check.startsWith('=')) {
      condition = `${varPath} = ${check.slice(1)}`
    } else if (check.startsWith('>=')) {
      condition = `${varPath} >= ${check.slice(2)}`
    } else if (check.startsWith('<=')) {
      condition = `${varPath} <= ${check.slice(2)}`
    } else if (check.startsWith('>')) {
      condition = `${varPath} > ${check.slice(1)}`
    } else if (check.startsWith('<')) {
      condition = `${varPath} < ${check.slice(1)}`
    } else if (check.startsWith('!=')) {
      condition = `${varPath} != ${check.slice(2)}`
    } else if (check.startsWith('~')) {
      condition = `${varPath} ~ ${check.slice(1)}`
    } else {
      condition = varPath
    }
    
    if (!falseVal || falseVal === '' || falseVal === "''" || falseVal === '""') {
      return `{if ${condition}}${trueVal}{/if}`
    }
    return `{if ${condition}}${trueVal}{else}${falseVal}{/if}`
  }
  
  // Apply conversion multiple times for nested patterns
  let prev = ''
  while (prev !== result) {
    prev = result
    result = result.replace(conditionalPattern, convertMatch)
  }
  
  // Convert simple modifiers: :: -> |
  result = result.replace(/::(\w+)/g, '|$1')
  
  return result
}

export function StreamFormatterConfig({ config, onChange }: ConfigSectionProps) {
  const [copied, setCopied] = useState<string | null>(null)
  const [converterOpen, setConverterOpen] = useState(false)
  const [aioInput, setAioInput] = useState('')
  const [convertedOutput, setConvertedOutput] = useState('')
  
  const currentTitle = config.st?.t ?? DEFAULT_TITLE_TEMPLATE
  const currentDescription = config.st?.d ?? DEFAULT_DESCRIPTION_TEMPLATE
  
  const updateTemplate = (field: 't' | 'd', value: string) => {
    onChange({
      ...config,
      st: {
        ...config.st,
        [field]: value,
      },
    })
  }
  
  const applyPreset = (presetKey: string) => {
    const preset = PRESETS[presetKey as keyof typeof PRESETS]
    if (preset) {
      onChange({
        ...config,
        st: {
          t: preset.title,
          d: preset.desc,
        },
      })
    }
  }
  
  const resetToDefault = () => {
    onChange({
      ...config,
      st: undefined,
    })
  }
  
  const copyField = (field: string) => {
    navigator.clipboard.writeText(`{${field}}`)
    setCopied(field)
    setTimeout(() => setCopied(null), 2000)
  }
  
  const handleConvert = () => {
    const converted = convertAIOStreamsToMediaFusion(aioInput)
    setConvertedOutput(converted)
  }
  
  const applyConvertedTitle = () => {
    if (convertedOutput) {
      updateTemplate('t', convertedOutput)
      setConverterOpen(false)
      setAioInput('')
      setConvertedOutput('')
    }
  }
  
  const applyConvertedDescription = () => {
    if (convertedOutput) {
      updateTemplate('d', convertedOutput)
      setConverterOpen(false)
      setAioInput('')
      setConvertedOutput('')
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Code className="h-5 w-5 text-primary" />
          Stream Formatter
        </CardTitle>
        <CardDescription>
          Customize how stream information is displayed in Stremio using templates
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Preset Selection */}
        <div className="space-y-3">
          <Label className="text-sm font-medium">Quick Presets</Label>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-2">
            {Object.entries(PRESETS).map(([key, preset]) => (
              <Button
                key={key}
                variant="outline"
                size="sm"
                className="h-auto py-2 px-3 flex flex-col items-start gap-0.5"
                onClick={() => applyPreset(key)}
              >
                <span className="font-medium text-xs">{preset.name}</span>
                <span className="text-[10px] text-muted-foreground truncate max-w-full">
                  {preset.description}
                </span>
              </Button>
            ))}
          </div>
        </div>
        
        <Separator />
        
        {/* Title Template */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="title-template" className="text-sm font-medium">
              Title Template
            </Label>
            <Badge variant="secondary" className="text-xs">
              Shows as stream title
            </Badge>
          </div>
          <Textarea
            id="title-template"
            value={currentTitle}
            onChange={(e) => updateTemplate('t', e.target.value)}
            placeholder="Enter title template..."
            className="font-mono text-sm h-20 resize-none"
          />
        </div>
        
        {/* Description Template */}
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="desc-template" className="text-sm font-medium">
              Description Template
            </Label>
            <Badge variant="secondary" className="text-xs">
              Shows as stream details
            </Badge>
          </div>
          <Textarea
            id="desc-template"
            value={currentDescription}
            onChange={(e) => updateTemplate('d', e.target.value)}
            placeholder="Enter description template..."
            className="font-mono text-sm h-40 resize-none"
          />
        </div>
        
        {/* Action Buttons */}
        <div className="flex justify-between items-center">
          {/* AIOStreams Converter */}
          <Dialog open={converterOpen} onOpenChange={setConverterOpen}>
            <DialogTrigger asChild>
              <Button variant="outline" size="sm" className="gap-2">
                <ArrowRightLeft className="h-4 w-4" />
                Import from AIOStreams
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-[600px]">
              <DialogHeader>
                <DialogTitle className="flex items-center gap-2">
                  <Sparkles className="h-5 w-5 text-amber-500" />
                  Convert AIOStreams Template
                </DialogTitle>
                <DialogDescription>
                  Paste your AIOStreams template below to convert it to MediaFusion format
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4 py-4">
                <div className="space-y-2">
                  <Label htmlFor="aio-input" className="text-sm font-medium">
                    AIOStreams Template
                  </Label>
                  <Textarea
                    id="aio-input"
                    value={aioInput}
                    onChange={(e) => setAioInput(e.target.value)}
                    placeholder={`Paste AIOStreams template here...\n\nExample:\n{stream.type::=torrent["{service.shortName}"||'']}`}
                    className="font-mono text-xs h-32 resize-none"
                  />
                </div>
                
                <Button onClick={handleConvert} className="w-full gap-2">
                  <ArrowRightLeft className="h-4 w-4" />
                  Convert to MediaFusion
                </Button>
                
                {convertedOutput && (
                  <div className="space-y-2">
                    <Label className="text-sm font-medium text-emerald-600">
                      ✅ Converted MediaFusion Template
                    </Label>
                    <Textarea
                      value={convertedOutput}
                      readOnly
                      className="font-mono text-xs h-32 resize-none bg-emerald-50 dark:bg-emerald-950/20 border-emerald-200 dark:border-emerald-800"
                    />
                  </div>
                )}
              </div>
              <DialogFooter className="gap-2">
                <Button variant="outline" onClick={() => setConverterOpen(false)}>
                  Cancel
                </Button>
                {convertedOutput && (
                  <>
                    <Button variant="secondary" onClick={applyConvertedTitle}>
                      Apply as Title
                    </Button>
                    <Button onClick={applyConvertedDescription}>
                      Apply as Description
                    </Button>
                  </>
                )}
              </DialogFooter>
            </DialogContent>
          </Dialog>
          
          <Button
            variant="outline"
            size="sm"
            onClick={resetToDefault}
            className="gap-2"
          >
            <RotateCcw className="h-4 w-4" />
            Reset to Default
          </Button>
        </div>
        
        <Separator />
        
        {/* Reference Documentation */}
        <Accordion type="single" collapsible className="w-full">
          <AccordionItem value="fields">
            <AccordionTrigger className="text-sm font-medium">
              <div className="flex items-center gap-2">
                <Eye className="h-4 w-4 text-blue-500" />
                Available Fields (Click to Copy)
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4">
              {Object.entries(FIELD_GROUPS).map(([key, group]) => (
                <div key={key}>
                  <h4 className="text-xs font-medium text-muted-foreground mb-2">{group.label}</h4>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-1.5">
                    {group.fields.map((item) => (
                      <button
                        key={item.field}
                        onClick={() => copyField(item.field)}
                        className={cn(
                          "flex items-center justify-between gap-2 p-2 rounded-lg text-left transition-colors",
                          "hover:bg-muted/80 bg-muted/40",
                          copied === item.field && "bg-emerald-500/20"
                        )}
                      >
                        <div className="min-w-0">
                          <code className="text-xs font-medium truncate block">
                            {'{' + item.field + '}'}
                          </code>
                          <span className="text-[10px] text-muted-foreground truncate block">
                            {item.description}
                          </span>
                        </div>
                        {copied === item.field ? (
                          <Check className="h-3.5 w-3.5 text-emerald-500 shrink-0" />
                        ) : (
                          <Copy className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                        )}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </AccordionContent>
          </AccordionItem>
          
          <AccordionItem value="syntax">
            <AccordionTrigger className="text-sm font-medium">
              <div className="flex items-center gap-2">
                <Wand2 className="h-4 w-4 text-primary" />
                Template Syntax & Modifiers
              </div>
            </AccordionTrigger>
            <AccordionContent className="space-y-4">
              {/* Syntax Examples */}
              <div className="space-y-3">
                {SYNTAX_EXAMPLES.map((section) => (
                  <div key={section.category} className="p-3 rounded-lg bg-muted/50">
                    <h4 className="font-medium mb-2 text-sm">{section.category}</h4>
                    <div className="space-y-1.5">
                      {section.examples.map((ex, i) => (
                        <div key={i} className="flex items-start gap-2 text-xs">
                          <code className="bg-background px-1.5 py-0.5 rounded shrink-0 text-[11px]">
                            {ex.code}
                          </code>
                          <span className="text-muted-foreground">{ex.desc}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
              
              {/* Modifiers */}
              <div>
                <h4 className="text-sm font-medium mb-2">Available Modifiers</h4>
                <div className="grid grid-cols-2 gap-1.5">
                  {MODIFIERS.map((mod) => (
                    <div key={mod.modifier} className="flex items-start gap-2 text-xs p-1.5 rounded bg-muted/30">
                      <code className="bg-background px-1 py-0.5 rounded shrink-0 text-[10px]">
                        {mod.modifier}
                      </code>
                      <span className="text-muted-foreground text-[10px]">{mod.description}</span>
                    </div>
                  ))}
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
        </Accordion>
      </CardContent>
    </Card>
  )
}
