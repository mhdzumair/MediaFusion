import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { AlertCircle, ExternalLink, Loader2 } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkBreaks from 'remark-breaks'
import remarkGfm from 'remark-gfm'
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from '@/components/ui/accordion'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { ScrollArea, ScrollBar } from '@/components/ui/scroll-area'
import { getReleaseNotes, type ReleaseNotesResponse } from '@/lib/api'
import { useInstance } from '@/contexts/InstanceContext'

const GITHUB_RELEASES_URL = 'https://github.com/mhdzumair/MediaFusion/releases'
const RELEASES_PER_PAGE = 8

const REACTION_LABELS: Array<{ key: keyof ReleaseNotesResponse['releases'][number]['reactions']; icon: string }> = [
  { key: '+1', icon: '👍' },
  { key: '-1', icon: '👎' },
  { key: 'heart', icon: '❤️' },
  { key: 'rocket', icon: '🚀' },
  { key: 'hooray', icon: '🎉' },
  { key: 'laugh', icon: '😄' },
  { key: 'confused', icon: '😕' },
  { key: 'eyes', icon: '👀' },
]

function normalizeReleaseMarkdown(body: string): string {
  return body.replace(/\r\n/g, '\n').replace(/(^|\n)(https?:\/\/[^\s]+)(?=\n|$)/g, '$1<$2>')
}

function extractReleasePreview(markdownBody: string): string {
  const previewLine = markdownBody
    .split('\n')
    .map((line) => line.trim())
    .find((line) => {
      if (!line) return false
      if (/^#{1,6}\s/.test(line)) return false
      if (/^([-*+]|\d+\.)\s/.test(line)) return false
      if (/^https?:\/\//.test(line) || /^<https?:\/\//.test(line)) return false
      return true
    })

  if (!previewLine) return 'Expand to read full release details.'
  return previewLine.replace(/^>\s?/, '')
}

export function ReleaseNotesPage() {
  const { instanceInfo } = useInstance()
  const [notesData, setNotesData] = useState<ReleaseNotesResponse | null>(null)
  const [isLoadingMore, setIsLoadingMore] = useState(false)
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null)
  const [openReleaseId, setOpenReleaseId] = useState<string | undefined>(undefined)
  const releaseItemRefs = useRef<Record<string, HTMLDivElement | null>>({})

  const {
    data: initialReleaseNotes,
    isLoading,
    isError,
    error,
  } = useQuery({
    queryKey: ['releaseNotes', RELEASES_PER_PAGE],
    queryFn: () => getReleaseNotes({ page: 1, perPage: RELEASES_PER_PAGE }),
    staleTime: 5 * 60 * 1000,
  })

  useEffect(() => {
    if (!initialReleaseNotes) return
    setNotesData(initialReleaseNotes)
    setLoadMoreError(null)
  }, [initialReleaseNotes])

  useEffect(() => {
    if (!notesData?.releases?.length) return
    if (openReleaseId) return
    const firstRelease = notesData.releases[0]
    setOpenReleaseId(`release-${firstRelease.tag_name}-0`)
  }, [notesData, openReleaseId])

  if (isLoading && !notesData) {
    return (
      <div className="max-w-4xl mx-auto py-8">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            Loading release notes...
          </CardContent>
        </Card>
      </div>
    )
  }

  if ((isError || !notesData) && !notesData) {
    return (
      <div className="max-w-4xl mx-auto py-8 space-y-4">
        <h1 className="text-3xl font-bold">Release Notes</h1>
        <Card>
          <CardContent className="p-6 space-y-3">
            <div className="flex items-center gap-2 text-destructive">
              <AlertCircle className="h-5 w-5" />
              <span className="font-medium">Unable to load release notes right now.</span>
            </div>
            <p className="text-sm text-muted-foreground">
              {(error as Error | undefined)?.message || 'The GitHub release service may be temporarily unavailable.'}
            </p>
            <a
              href={GITHUB_RELEASES_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sm text-primary underline hover:text-primary/80"
            >
              View releases on GitHub
              <ExternalLink className="h-3.5 w-3.5" />
            </a>
          </CardContent>
        </Card>
      </div>
    )
  }

  const releases = notesData.releases ?? []

  const handleLoadMore = async () => {
    if (!notesData?.has_more || isLoadingMore) return

    setIsLoadingMore(true)
    setLoadMoreError(null)
    try {
      const nextPage = notesData.page + 1
      const nextPageData = await getReleaseNotes({
        page: nextPage,
        perPage: notesData.per_page,
      })

      setNotesData((previous) => {
        if (!previous) return nextPageData

        const existingTags = new Set(previous.releases.map((release) => release.tag_name))
        const newUniqueReleases = nextPageData.releases.filter((release) => !existingTags.has(release.tag_name))

        return {
          ...nextPageData,
          releases: [...previous.releases, ...newUniqueReleases],
        }
      })
    } catch (loadError) {
      setLoadMoreError((loadError as Error)?.message || 'Failed to load older release notes.')
    } finally {
      setIsLoadingMore(false)
    }
  }

  return (
    <div className="w-full max-w-6xl mx-auto py-8 space-y-6">
      <div className="space-y-2">
        <h1 className="text-3xl font-bold">What&apos;s New?</h1>
        <p className="text-muted-foreground">Showing both stable and pre-release notes directly from GitHub.</p>
      </div>

      <Card>
        <CardContent className="p-6 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="secondary">Server v{instanceInfo?.version || '...'}</Badge>
            <Badge variant="outline">Stable + Pre-release</Badge>
            <Badge variant="outline">{releases.length} loaded</Badge>
          </div>
          <a
            href={GITHUB_RELEASES_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-sm text-primary underline hover:text-primary/80"
          >
            Open full releases on GitHub
            <ExternalLink className="h-3.5 w-3.5" />
          </a>
        </CardContent>
      </Card>

      {releases.length === 0 ? (
        <Card>
          <CardContent className="p-6 text-muted-foreground">
            No release notes were found for this channel yet.
          </CardContent>
        </Card>
      ) : (
        <Accordion
          type="single"
          collapsible
          value={openReleaseId}
          onValueChange={(value) => {
            setOpenReleaseId(value || undefined)
            if (!value) return

            window.requestAnimationFrame(() => {
              releaseItemRefs.current[value]?.scrollIntoView({
                behavior: 'smooth',
                block: 'start',
              })
            })
          }}
          className="space-y-4"
        >
          {releases.map((release, index) => {
            const publishedAt = release.published_at ? new Date(release.published_at).toLocaleString() : 'Unknown date'
            const normalizedBody = normalizeReleaseMarkdown(
              release.body?.trim() || 'No release notes were provided for this release.',
            )
            const previewText = extractReleasePreview(normalizedBody)
            const releaseId = `release-${release.tag_name}-${index}`
            return (
              <Card
                key={`${release.tag_name}-${index}`}
                ref={(element) => {
                  releaseItemRefs.current[releaseId] = element
                }}
                className="scroll-mt-20"
              >
                <CardContent className="p-0">
                  <AccordionItem value={releaseId} className="border-b-0">
                    <AccordionTrigger className="px-6 py-5 hover:no-underline">
                      <div className="text-left space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <h2 className="text-lg font-semibold">{release.name || release.tag_name}</h2>
                            <Badge variant={release.prerelease ? 'default' : 'outline'}>
                              {release.prerelease ? 'Pre-release' : 'Stable'}
                            </Badge>
                          </div>
                          <span className="text-xs text-muted-foreground">{publishedAt}</span>
                        </div>
                        <p className="text-sm text-muted-foreground">
                          Tag: <span className="font-mono">{release.tag_name}</span>
                        </p>
                        <p className="text-sm text-muted-foreground line-clamp-2">{previewText}</p>
                      </div>
                    </AccordionTrigger>
                    <AccordionContent className="px-6 pb-6 text-base">
                      <div className="space-y-4">
                        <a
                          href={release.html_url || GITHUB_RELEASES_URL}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1.5 text-sm text-primary underline hover:text-primary/80"
                        >
                          View this release on GitHub
                          <ExternalLink className="h-3.5 w-3.5" />
                        </a>

                        <article className="prose dark:prose-invert max-w-none prose-p:my-3 prose-li:my-1 prose-headings:mb-3 prose-headings:mt-6">
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm, remarkBreaks]}
                            components={{
                              h2: ({ children }) => <h2 className="mt-6 mb-3 text-xl font-semibold">{children}</h2>,
                              h3: ({ children }) => <h3 className="mt-5 mb-2 text-lg font-semibold">{children}</h3>,
                              p: ({ children }) => <p className="my-2 leading-relaxed">{children}</p>,
                              ul: ({ children }) => <ul className="my-3 list-disc pl-6 space-y-1">{children}</ul>,
                              li: ({ children }) => <li className="leading-relaxed">{children}</li>,
                              a: ({ href, children }) => (
                                <a
                                  href={href}
                                  target="_blank"
                                  rel="noopener noreferrer"
                                  className="text-primary underline hover:text-primary/80"
                                >
                                  {children}
                                </a>
                              ),
                              code: ({ children }) => (
                                <code className="rounded bg-muted px-1.5 py-0.5 text-[0.9em]">{children}</code>
                              ),
                              pre: ({ children }) => (
                                <ScrollArea className="rounded-lg border bg-muted/30">
                                  <pre className="p-4">{children}</pre>
                                  <ScrollBar orientation="horizontal" />
                                </ScrollArea>
                              ),
                            }}
                          >
                            {normalizedBody}
                          </ReactMarkdown>
                        </article>

                        {release.reactions.total_count > 0 && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            <span className="text-xs text-muted-foreground">Reactions:</span>
                            {REACTION_LABELS.filter(({ key }) => release.reactions[key] > 0).map(({ key, icon }) => (
                              <Badge key={`${release.tag_name}-${key}`} variant="outline" className="text-xs">
                                {icon} {release.reactions[key]}
                              </Badge>
                            ))}
                          </div>
                        )}
                      </div>
                    </AccordionContent>
                  </AccordionItem>
                </CardContent>
              </Card>
            )
          })}
        </Accordion>
      )}

      <div className="space-y-2">
        {notesData.has_more ? (
          <Button variant="outline" onClick={handleLoadMore} disabled={isLoadingMore}>
            {isLoadingMore ? (
              <>
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                Loading older notes...
              </>
            ) : (
              'Load older release notes'
            )}
          </Button>
        ) : (
          <p className="text-sm text-muted-foreground">You have reached the oldest available release notes.</p>
        )}
        {loadMoreError && <p className="text-sm text-destructive">{loadMoreError}</p>}
      </div>
    </div>
  )
}
