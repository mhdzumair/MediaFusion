import { test } from 'node:test'
import assert from 'node:assert/strict'
import { collectionFiles } from '../src/pages/ContentImport/utils/collectionFiles.ts'

test('collection files are available immediately, sorted, with physical indexes intact', () => {
  const files = collectionFiles({ files: [
    { filename: 'Movie10.mkv', index: 9, size: 100 },
    { filename: 'readme.txt', index: 0, size: 1 },
    { filename: 'Movie2.MP4', index: 7, size: 200 },
  ] })
  assert.deepEqual(files.map(f => [f.filename, f.index, f.size]), [['Movie2.MP4', 7, 200], ['Movie10.mkv', 9, 100]])
  assert.ok(files.every(f => f.included))
})

test('accepts full paths and resolved-only files without compacting indexes', () => {
  assert.equal(collectionFiles({ files: [{ filename: 'short.mkv', path: 'folder/full.mkv', index: 5, size: 1 }] })[0].filename, 'folder/full.mkv')
  const files = collectionFiles({ files: [], resolved: { files: [{ path: 'readme.txt', size: 1 }, { path: 'folder/movie.mkv', size: 2 }] } })
  assert.equal(files[0].index, 1)
  assert.equal(files[0].filename, 'folder/movie.mkv')
  assert.deepEqual(collectionFiles({ files: [] }), [])
})

test('Movie Collection wizard renders analyzed files on its first mount', async () => {
  const { build } = await import('esbuild')
  const { renderToStaticMarkup } = await import('react-dom/server')
  const { createElement } = await import('react')
  const { mkdtemp, rm } = await import('node:fs/promises')
  const { resolve } = await import('node:path')
  const { pathToFileURL } = await import('node:url')
  // Keep the temporary module inside this package so external React imports resolve.
  const directory = await mkdtemp(resolve('node_modules/.collection-test-'))
  try {
    const output = resolve(directory, 'wizard.mjs')
    await build({
      entryPoints: ['src/pages/ContentImport/components/MultiContentWizard.tsx'],
      outfile: output,
      bundle: true,
      platform: 'node',
      format: 'esm',
      packages: 'external',
      alias: { '@': resolve('src') },
      jsx: 'automatic',
      define: { 'import.meta.env': '{}' },
      logLevel: 'silent',
    })
    const { MultiContentWizard } = await import(pathToFileURL(output).href)
    const html = renderToStaticMarkup(createElement(MultiContentWizard, {
      analysis: { files: [{ filename: 'First-mount-movie.mkv', index: 7, size: 200 }] },
      importMode: 'collection', onComplete() {}, onCancel() {},
    }))
    assert.match(html, /First-mount-movie\.mkv/)
    assert.match(html, /1 video file/)
    assert.doesNotMatch(html, /No video files were resolved/)
  } finally {
    await rm(directory, { recursive: true, force: true })
  }
})
