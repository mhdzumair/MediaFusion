import { test } from 'node:test'
import assert from 'node:assert/strict'
import { streamResourceId, streamResourcePath, prepareStreamResponse, isProxyStream } from '../src/lib/streamResource.ts'

test('uses external IDs and episode suffixes in the path rather than the query', () => {
  assert.equal(streamResourcePath('movie', streamResourceId(42, { imdb: 'tt0063350' })), '/stream/movie/tt0063350.json')
  assert.equal(streamResourcePath('series', streamResourceId(42, { tmdb: '1399' }), 1, 2), '/stream/series/tmdb:1399:1:2.json')
  assert.equal(streamResourcePath('series', streamResourceId(42), 0, 1), '/stream/series/mf:42:0:1.json')
  assert.equal(streamResourceId(42, { kitsu: '123' }), 'kitsu:123')
})

test('MediaFlow uses the proxy-rewritten URL and ignores stale browser_url', () => {
  const url = 'https://operator.example/playback/wrapped-token'
  const response = prepareStreamResponse({
    streams: [{ name: 'Stream', url, browser_url: 'https://stale.example/raw-playback' }],
    web_playback: { proxy_url: 'https://mf.example/base/', api_password: 'password & spaces' },
  })
  assert.equal(response.streams[0].url, url)
  const browser = new URL(response.streams[0].browser_url)
  assert.equal(browser.pathname, '/base/proxy/stream')
  assert.equal(browser.searchParams.get('d'), url)
  assert.equal(browser.searchParams.get('api_password'), 'password & spaces')
  assert.equal(prepareStreamResponse({ streams: [{ name: 'No MF', url, browser_url: 'stale' }] }).streams[0].browser_url, undefined)
})

test('proxy-only entries are playable without any MediaFusion metadata', () => {
  const stream = { name: '🚫 N/A', description: 'Not available here\n▶️ Play for details', url: 'https://operator.example/playback/details' }
  assert.equal(isProxyStream(stream), true)
  assert.equal(isProxyStream({ ...stream, id: 42 }), false)
  assert.equal(isProxyStream({ name: 'No link' }), false)
  assert.equal(prepareStreamResponse({ streams: [stream] }).streams[0].url, stream.url)
})

test('minimal proxy rows render their message and playback controls', async () => {
  const { build } = await import('esbuild')
  const { renderToStaticMarkup } = await import('react-dom/server')
  const { createElement } = await import('react')
  const { mkdtemp, rm } = await import('node:fs/promises')
  const { resolve } = await import('node:path')
  const { pathToFileURL } = await import('node:url')
  const directory = await mkdtemp(resolve('node_modules/.proxy-test-'))
  try {
    const output = resolve(directory, 'card.mjs')
    await build({ entryPoints: ['src/components/stream/ProxyStreamCard.tsx'], outfile: output, bundle: true, platform: 'node', format: 'esm', packages: 'external', alias: { '@': resolve('src') }, jsx: 'automatic', define: { 'import.meta.env': '{}' }, logLevel: 'silent' })
    const { ProxyStreamCard } = await import(pathToFileURL(output).href)
    const html = renderToStaticMarkup(createElement(ProxyStreamCard, { stream: { name: '🚫 N/A', description: 'Not available here', url: 'https://operator.example/playback/details' }, onPlay() {} }))
    assert.match(html, /Not available here/)
    assert.match(html, /Copy URL/)
    assert.match(html, /Open externally/)
    assert.match(html, />Play</)
    assert.match(html, /https:\/\/operator.example\/playback\/details/)
  } finally { await rm(directory, { recursive: true, force: true }) }
})
