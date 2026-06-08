# Stream Formatting

MediaFusion lets you customize how streams appear in Stremio by writing your own **title** and **description** templates. Templates are stored as part of your user configuration under the `stream_template` key.

---

## Quick start

Set a custom template in the Configure UI or by encoding it in your profile's JSON:

```json
{
  "stream_template": {
    "title": "{addon.name} {if stream.resolution}{stream.resolution}{/if}",
    "description": "⭐ {meta.imdb_rating} · 📦 {stream.size|bytes}\n🔗 {stream.source}"
  }
}
```

Use the short-form keys `t` / `d` to keep the URL shorter:

```json
{ "stream_template": { "t": "...", "d": "..." } }
```

Either key form is accepted. Leave a key empty (`""`) or omit it entirely to keep the built-in default for that half.

---

## Template syntax

### Variables

Access a property with dot notation:

```
{stream.resolution}     → "1080p"
{meta.imdb_rating}      → 8.5
{service.shortName}     → "RD"
```

Deeply nested paths and missing keys both produce an empty string — templates never error.

### Conditionals

```
{if stream.cached}⚡️{else}⏳{/if}

{if stream.type = torrent}🧲{elif stream.type = usenet}📰{else}🌐{/if}

{if meta.imdb_rating >= 8}🔥 {meta.imdb_rating}{/if}
```

Supported comparison operators: `=` `!=` `>` `<` `>=` `<=`
String match operators: `~` (contains), `$` (starts with), `^` (ends with)
Logical: `and`, `or`, `not`

### Modifiers

Chain modifiers with `|`:

```
{stream.size|bytes}         → "4.2 GB"
{stream.languages|join(' + ')}   → "English + French"
{meta.title|upper}          → "INCEPTION"
{stream.source|truncate(20)}
```

| Modifier | Input | Output |
|---|---|---|
| `bytes` | integer (bytes) | `4.2 GB` |
| `time` | integer (seconds) | `1:42:30` |
| `upper` | string | `UPPERCASE` |
| `lower` | string | `lowercase` |
| `title` | string | `Title Case` |
| `first` | array | first element |
| `last` | array | last element |
| `join(sep)` | array | elements joined by separator |
| `truncate(n)` | string | truncated to n chars + `...` |
| `replace(old,new)` | string | substring replaced |
| `escape` / `e` | string | HTML-escaped |

---

## Variable reference

### `stream.*` — the stream itself

These fields are available for all stream types. Fields marked with a stream type are only present for that type.

**Strings**

| Variable | Description | Types |
|---|---|---|
| `stream.type` | Stream type: `torrent` `usenet` `http` `youtube` `telegram` | all |
| `stream.name` | Original release / file name | all |
| `stream.filename` | Selected file name within the torrent | torrent |
| `stream.resolution` | `4k` `2160p` `1080p` `720p` `480p` … | all |
| `stream.quality` | `bluray` `web-dl` `webrip` `hdtv` `cam` … | all |
| `stream.codec` | `x264` `x265` `hevc` `av1` … | all |
| `stream.source` | Scraper / indexer name | all |
| `stream.uploader` | Contributor name | torrent |
| `stream.release_group` | PTT-parsed release group | torrent |
| `stream.bit_depth` | `8-bit` `10-bit` … | torrent |

**Numbers**

| Variable | Description | Types |
|---|---|---|
| `stream.size` | File size in bytes | torrent usenet telegram http |
| `stream.folderSize` | Total torrent folder size in bytes | torrent |
| `stream.seeders` | Seeder count | torrent |

**Booleans**

| Variable | Description | Types |
|---|---|---|
| `stream.cached` | Cached on debrid provider | torrent usenet |
| `stream.is_proper` | Proper/fixed release | torrent |
| `stream.is_repack` | Repack of prior release | torrent |
| `stream.is_extended` | Extended edition | torrent |
| `stream.is_complete` | Complete collection | torrent |
| `stream.is_dubbed` | Dubbed audio | torrent |
| `stream.is_subbed` | Has subtitles | torrent |
| `stream.is_remastered` | Remastered content | torrent |
| `stream.is_upscaled` | AI-upscaled content | torrent |

**Arrays** (use `|join(sep)` to render)

| Variable | Description | Types |
|---|---|---|
| `stream.audio_formats` | `AAC` `DTS` `Atmos` `TrueHD` `EAC3` … | torrent |
| `stream.channels` | `2.0` `5.1` `7.1` … | torrent |
| `stream.hdr_formats` | `HDR10` `HDR10+` `Dolby Vision` `HLG` … | torrent |
| `stream.languages` | Full language names | torrent telegram http youtube |
| `stream.language_flags` | Flag emoji per language | torrent |

---

### `service.*` — the debrid/usenet provider

Available for torrent and usenet streams only (empty object for HTTP, YouTube, Telegram).

| Variable | Description |
|---|---|
| `service.name` | Full provider name (e.g. `realdebrid`) |
| `service.shortName` | Short code (e.g. `RD`) |
| `service.cached` | Whether the stream is cached on this provider |

---

### `addon.*` — the addon instance

| Variable | Description |
|---|---|
| `addon.name` | Name configured for this MediaFusion instance |

---

### `meta.*` — the media being played

Available for all stream types. Fields are absent (not null) when data is missing, so `{if meta.imdb_rating}` is safe.

| Variable | Type | Description |
|---|---|---|
| `meta.title` | string | Movie or show title |
| `meta.type` | string | `movie` or `series` |
| `meta.year` | integer | Release / start year |
| `meta.end_year` | integer | End year for finished series |
| `meta.imdb_id` | string | IMDb ID (e.g. `tt0816692`) |
| `meta.tmdb_id` | string | TMDB ID |
| `meta.imdb_rating` | float | IMDb rating (e.g. `8.6`) |
| `meta.runtime_minutes` | integer | Runtime in minutes |
| `meta.language` | string | Original language code |
| `meta.country` | string | Country of origin |
| `meta.description` | string | Synopsis / overview |
| `meta.website` | string | Official website URL |
| `meta.poster_url` | string | Poster image URL |
| `meta.background_url` | string | Background/fanart image URL |
| `meta.season` | integer | Season number (series requests only) |
| `meta.episode` | integer | Episode number (series requests only) |

---

## Default templates

These are the built-in templates used when no custom template is set:

**Default title:**
```
{addon.name} {if stream.type = torrent}🧲 {service.shortName} {if service.cached}⚡️{else}⏳{/if}{elif stream.type = usenet}📰 {service.shortName}{elif stream.type = telegram}📱{elif stream.type = youtube}▶️{elif stream.type = http}🌐{else}🔗{/if} {if stream.resolution}{stream.resolution}{/if}
```

**Default description:**
```
{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality} {/if}{if stream.codec}🎞️ {stream.codec} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')} {/if}{if stream.channels}🔊 {stream.channels|join(' ')}{/if}
{if stream.size > 0}📦 {stream.size|bytes}{if stream.folderSize > stream.size} / {stream.folderSize|bytes}{/if} {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}
{if stream.languages}🌐 {stream.languages|join(' + ')}{/if}
🔗 {stream.source}{if stream.uploader} | 🧑‍💻 {stream.uploader}{/if}
```

---

## Example recipes

### Compact title with rating

Shows the IMDb rating next to the resolution, falling back gracefully when either is absent:

```
{addon.name} {if meta.imdb_rating}⭐{meta.imdb_rating} {/if}{if stream.resolution}{stream.resolution}{/if}
```

### Rating and runtime in description

```
{if meta.imdb_rating}⭐ {meta.imdb_rating}/10  {/if}{if meta.runtime_minutes}⏱ {meta.runtime_minutes|time}{/if}
{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.audio_formats}🎵 {stream.audio_formats|join('|')}{/if}
📦 {stream.size|bytes}  🔗 {stream.source}
```

### Series-aware template

Appends season/episode numbers to the description when available:

```json
{
  "t": "{addon.name} {if service.shortName}{service.shortName} {/if}{if stream.resolution}{stream.resolution}{/if}",
  "d": "{if meta.season}S{meta.season}E{meta.episode}  {/if}{if stream.size > 0}📦 {stream.size|bytes}  {/if}{if stream.seeders > 0}👤 {stream.seeders}{/if}\n🔗 {stream.source}"
}
```

### Language-first layout

Puts languages at the top of the description:

```
{if stream.language_flags}{stream.language_flags|join(' ')} {stream.languages|join(' + ')}{/if}
{if stream.hdr_formats}🎨 {stream.hdr_formats|join('|')} {/if}{if stream.quality}📺 {stream.quality}{/if}
📦 {stream.size|bytes}  🔗 {stream.source}
```

### Show only cached streams prominently

```
{addon.name} {if service.cached}⚡️{service.shortName}{else}⏳{service.shortName}{/if} {stream.resolution}
```

### Highlight release-quality flags

```
{if stream.is_proper}✅ PROPER  {/if}{if stream.is_repack}🔁 REPACK  {/if}{if stream.is_extended}✂️ EXTENDED  {/if}{if stream.is_dubbed}🗣️ DUBBED{/if}
{if stream.hdr_formats}{stream.hdr_formats|join('|')} · {/if}{stream.codec} · 📦 {stream.size|bytes}
🔗 {stream.source}
```

---

## Tips

- **Blank lines**: The renderer strips blank lines from the output. Use `\n` (a literal newline inside the template string) to control spacing.
- **Missing fields**: Any variable that is absent or null produces an empty string. Wrapping in `{if ...}` prevents stray spaces or emoji from appearing.
- **Template length**: Maximum 10 000 characters per template.
- **Testing**: Use the `/api/v1/streams` endpoint (if enabled on your instance) with `encoded_user_data` headers to test template output without going through Stremio.
