// Torrent types
export const TORRENT_TYPES = [
  { value: 'public', label: 'Public' },
  { value: 'private', label: 'Private' },
  { value: 'webseed', label: 'WebSeed' },
] as const

// Catalog options for pattern matching
export const CATALOG_OPTIONS = {
  movies: [
    { id: 'tamil_hdrip', name: 'Tamil HD Movies' },
    { id: 'tamil_tcrip', name: 'Tamil TC Movies' },
    { id: 'tamil_dubbed', name: 'Tamil Dubbed Movies' },
    { id: 'hindi_hdrip', name: 'Hindi HD Movies' },
    { id: 'hindi_tcrip', name: 'Hindi TC Movies' },
    { id: 'hindi_dubbed', name: 'Hindi Dubbed Movies' },
    { id: 'telugu_hdrip', name: 'Telugu HD Movies' },
    { id: 'telugu_tcrip', name: 'Telugu TC Movies' },
    { id: 'telugu_dubbed', name: 'Telugu Dubbed Movies' },
    { id: 'malayalam_hdrip', name: 'Malayalam HD Movies' },
    { id: 'malayalam_tcrip', name: 'Malayalam TC Movies' },
    { id: 'malayalam_dubbed', name: 'Malayalam Dubbed Movies' },
    { id: 'kannada_hdrip', name: 'Kannada HD Movies' },
    { id: 'kannada_tcrip', name: 'Kannada TC Movies' },
    { id: 'english_hdrip', name: 'English HD Movies' },
    { id: 'english_tcrip', name: 'English TC Movies' },
  ],
  series: [
    { id: 'tamil_series', name: 'Tamil Series' },
    { id: 'hindi_series', name: 'Hindi Series' },
    { id: 'telugu_series', name: 'Telugu Series' },
    { id: 'malayalam_series', name: 'Malayalam Series' },
    { id: 'kannada_series', name: 'Kannada Series' },
    { id: 'english_series', name: 'English Series' },
  ],
  sports: [
    { id: 'sports.football', name: 'Football' },
    { id: 'sports.cricket', name: 'Cricket' },
    { id: 'sports.f1', name: 'Formula 1' },
    { id: 'sports.nfl', name: 'NFL' },
    { id: 'sports.afl', name: 'AFL' },
    { id: 'sports.wwe', name: 'WWE' },
  ],
}

// All catalogs in a flat list
export const ALL_CATALOGS = [
  ...CATALOG_OPTIONS.movies,
  ...CATALOG_OPTIONS.series,
  ...CATALOG_OPTIONS.sports,
]

// Parsing pattern fields configuration
export const PARSING_PATTERN_FIELDS = [
  { key: 'title', label: 'Title', placeholder: 'title', hasRegex: false },
  { key: 'description', label: 'Description', placeholder: 'description', hasRegex: false },
  { key: 'pubDate', label: 'Publish Date', placeholder: 'pubDate', hasRegex: false },
  { key: 'poster', label: 'Poster', placeholder: 'poster or image', hasRegex: false },
  { key: 'background', label: 'Background', placeholder: 'background', hasRegex: false },
  { key: 'logo', label: 'Logo', placeholder: 'logo', hasRegex: false },
  { key: 'category', label: 'Category', placeholder: 'category', hasRegex: true },
  { key: 'magnet', label: 'Magnet Link', placeholder: 'torznab:attr[@name="magneturl"]@value', hasRegex: true },
  { key: 'torrent', label: 'Torrent Link', placeholder: 'enclosure.@url or link', hasRegex: true },
  { key: 'info_hash', label: 'Info Hash (direct)', placeholder: 'torznab:attr[@name="infohash"]@value', hasRegex: true },
  { key: 'size', label: 'Size', placeholder: 'size or torznab:attr[@name="size"]@value', hasRegex: true },
  { key: 'seeders', label: 'Seeders', placeholder: 'torznab:attr[@name="seeders"]@value', hasRegex: true },
  { key: 'episode_name_parser', label: 'Episode Name Parser', placeholder: 'Regex for episode parsing', hasRegex: false },
] as const

// Filter fields configuration
export const FILTER_FIELDS = [
  { key: 'title_filter', label: 'Title Include Filter', placeholder: 'Regex to include titles', type: 'text' },
  { key: 'title_exclude_filter', label: 'Title Exclude Filter', placeholder: 'Regex to exclude titles', type: 'text' },
  { key: 'min_size_mb', label: 'Minimum Size (MB)', placeholder: '0', type: 'number' },
  { key: 'max_size_mb', label: 'Maximum Size (MB)', placeholder: 'No limit', type: 'number' },
  { key: 'min_seeders', label: 'Minimum Seeders', placeholder: '0', type: 'number' },
  { key: 'category_filter', label: 'Category Filter', placeholder: 'Movies, Series (comma separated)', type: 'text' },
] as const




