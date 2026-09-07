import { test } from 'node:test'
import assert from 'node:assert/strict'
import { detectEpisodeMetadata } from '../src/pages/ContentImport/utils/episodeMetadata.ts'

test('S10 EP01 keeps season 10 in the reported release title', () => {
  assert.deepEqual(detectEpisodeMetadata('www.1TamilMV.meme - BIGG BOSS (Tamil) S10 EP01 DAY 00 TRUE WEB-DL - 480p - AVC - AAC - 450MB.mkv'), { season_number: 10, episode_number: 1, episode_end: null })
})
test('common separators, specials and episode ranges', () => {
  for (const name of ['Show.S10.EP01.mkv', 'Show_S10_EP01.mkv', 'Show S10-EP01.mkv', 'Show.S10E01.mkv']) {
    assert.deepEqual(detectEpisodeMetadata(name), { season_number: 10, episode_number: 1, episode_end: null })
  }
  assert.deepEqual(detectEpisodeMetadata('Show.S00.EP01-EP03.mkv'), { season_number: 0, episode_number: 1, episode_end: 3 })
  assert.deepEqual(detectEpisodeMetadata('Show.S02E03-E05.mkv'), { season_number: 2, episode_number: 3, episode_end: 5 })
})
test('quality and day numbers are not episode ranges; episode-only does not invent a season', () => {
  assert.deepEqual(detectEpisodeMetadata('Show Season 10 Episode 1 1080p.mkv'), { season_number: 10, episode_number: 1, episode_end: null })
  assert.deepEqual(detectEpisodeMetadata('Show EP01.mkv'), { season_number: null, episode_number: 1, episode_end: null })
})
