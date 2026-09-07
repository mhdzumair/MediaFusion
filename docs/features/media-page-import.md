# Import streams from a media page

Signed-in users can import directly from movie and series detail pages. The **Add a stream** card is available even when the item has no streams or episodes.

- Drop up to 20 `.torrent` or `.nzb` files onto the card, or choose them with **Import stream** (up to 20 MB per file). Each source is reviewed and imported in sequence.
- Each series episode row also accepts up to 20 file drops and has an upload button. Single-file imports default to that row’s season and episode; packs retain per-file detection for review.
- Paste a magnet link or HTTP/HTTPS stream URL in **Import stream**.
- Review resolution, quality, codec, audio, HDR, languages and catalogs. The destination is the current media item; metadata discovery is skipped.
- For series, review the file annotations, exclude unwanted files, and assign seasons, episodes and optional episode ends. Season 0 supports specials. Packs can include multiple seasons. An HTTP stream defaults to the episode selected on the page; file imports retain filename detection for review.
- Confirm the import. Normal upload restrictions, rate limits, duplicate handling and anonymous contribution moderation still apply. Pending submissions are identified in the result message.

If a magnet's files cannot be resolved, upload its `.torrent` file to map series episodes. Other source types remain available on the dedicated Content Import page.

## API

Torrent/magnet analyze and import endpoints, NZB file analyze/import, and HTTP import accept optional `target_media_id` (a positive INT4 media ID). The server checks that the item exists and its stored type matches `meta_type`. Scoped submissions attach directly to this ID and do not fetch or replace its metadata. Ordinary imports remain supported when the field is omitted.

Series torrent/NZB submissions use `file_data` with original source indices, `included`, `season_number`, `episode_number`, and optional `episode_end`. Scoped imports reject empty selections, incomplete mappings, duplicate indices and per-file media overrides. Torrent uploads verify indices against the uploaded file. Episode ranges create links for every episode in the range.

HTTP import accepts JSON, including `season_number`, `episode_number` and optional `episode_end`. Its multi-value `languages`, `audio`, `hdr` and `catalogs` fields are arrays. The frontend client converts its comma-separated form values before submission.

Successful scoped imports invalidate Redis stream caches for the destination; the UI refreshes media details and streams.

## Verification

```sh
SQLX_OFFLINE=true cargo test --manifest-path backend/Cargo.toml --lib media_import_tests
cd clients/frontend
npm run build
node --experimental-strip-types --test tests/episodeMetadata.test.mjs
```

Manual/browser regression cases: movie import without discovery, multi-season torrent with excluded files and original indices preserved, reopening annotations, episode ranges and specials, NZB pending moderation, unresolved magnets, HTTP imports, failed import retry, keyboard file selection and narrow screens.
