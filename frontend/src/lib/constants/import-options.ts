// Import options constants for Content Import page
// Based on legacy scraperControl.js SPEC_OPTIONS

export const RESOLUTION_OPTIONS = [
  { value: '480p', label: '480p (SD)' },
  { value: '576p', label: '576p (SD)' },
  { value: '720p', label: '720p (HD)' },
  { value: '1080p', label: '1080p (Full HD)' },
  { value: '1440p', label: '1440p (2K)' },
  { value: '2160p', label: '2160p (4K)' },
  { value: '4K', label: '4K UHD' },
] as const

export const QUALITY_OPTIONS = [
  { value: 'BluRay', label: 'BluRay' },
  { value: 'BluRay REMUX', label: 'BluRay REMUX' },
  { value: 'BRRip', label: 'BRRip' },
  { value: 'BDRip', label: 'BDRip' },
  { value: 'WEB-DL', label: 'WEB-DL' },
  { value: 'WEBRip', label: 'WEBRip' },
  { value: 'HDRip', label: 'HDRip' },
  { value: 'DVDRip', label: 'DVDRip' },
  { value: 'HDTV', label: 'HDTV' },
  { value: 'CAM', label: 'CAM' },
  { value: 'TeleSync', label: 'TeleSync' },
  { value: 'SCR', label: 'SCR' },
] as const

export const CODEC_OPTIONS = [
  { value: 'x264', label: 'x264' },
  { value: 'x265', label: 'x265 (HEVC)' },
  { value: 'h.264', label: 'H.264 (AVC)' },
  { value: 'h.265', label: 'H.265 (HEVC)' },
  { value: 'hevc', label: 'HEVC' },
  { value: 'avc', label: 'AVC' },
  { value: 'av1', label: 'AV1' },
  { value: 'mpeg-2', label: 'MPEG-2' },
  { value: 'mpeg-4', label: 'MPEG-4' },
  { value: 'vp9', label: 'VP9' },
] as const

export const AUDIO_OPTIONS = [
  { value: 'AAC', label: 'AAC' },
  { value: 'AC3', label: 'AC3 (Dolby Digital)' },
  { value: 'EAC3', label: 'EAC3 (Dolby Digital Plus)' },
  { value: 'DTS', label: 'DTS' },
  { value: 'DTS-HD MA', label: 'DTS-HD MA' },
  { value: 'TrueHD', label: 'Dolby TrueHD' },
  { value: 'Atmos', label: 'Dolby Atmos' },
  { value: 'DD+', label: 'DD+' },
  { value: 'DTS Lossless', label: 'DTS Lossless' },
  { value: 'FLAC', label: 'FLAC' },
  { value: 'PCM', label: 'PCM' },
  { value: 'MP3', label: 'MP3' },
  { value: 'Opus', label: 'Opus' },
] as const

export const HDR_OPTIONS = [
  { value: 'DV', label: 'Dolby Vision' },
  { value: 'HDR10+', label: 'HDR10+' },
  { value: 'HDR10', label: 'HDR10' },
  { value: 'HDR', label: 'HDR' },
  { value: 'HLG', label: 'HLG' },
  { value: 'SDR', label: 'SDR' },
] as const

export const CONTENT_TYPE_OPTIONS = [
  { value: 'movie', label: 'Movie', description: 'Single film content' },
  { value: 'series', label: 'Series', description: 'TV show with episodes' },
  { value: 'sports', label: 'Sports', description: 'Sports event content' },
  { value: 'tv', label: 'Live TV', description: 'Live TV channel' },
] as const

// Import mode options for multi-content support
export const IMPORT_MODE_OPTIONS = {
  movie: [
    { value: 'single', label: 'Single Movie', description: 'One movie file' },
    { value: 'collection', label: 'Movie Collection', description: 'Multiple movies (e.g., MCU Phase 1)' },
  ],
  series: [
    { value: 'single', label: 'Single Series', description: 'Episodes of one TV show' },
    { value: 'pack', label: 'Series Pack', description: 'Multiple different series' },
  ],
} as const

export type ImportMode = 'single' | 'collection' | 'pack'

export const LANGUAGE_OPTIONS = [
  { value: 'English', label: 'English' },
  { value: 'Tamil', label: 'Tamil' },
  { value: 'Hindi', label: 'Hindi' },
  { value: 'Malayalam', label: 'Malayalam' },
  { value: 'Kannada', label: 'Kannada' },
  { value: 'Telugu', label: 'Telugu' },
  { value: 'Chinese', label: 'Chinese' },
  { value: 'Russian', label: 'Russian' },
  { value: 'Arabic', label: 'Arabic' },
  { value: 'Japanese', label: 'Japanese' },
  { value: 'Korean', label: 'Korean' },
  { value: 'Taiwanese', label: 'Taiwanese' },
  { value: 'Latino', label: 'Latino' },
  { value: 'French', label: 'French' },
  { value: 'Spanish', label: 'Spanish' },
  { value: 'Portuguese', label: 'Portuguese' },
  { value: 'Italian', label: 'Italian' },
  { value: 'German', label: 'German' },
  { value: 'Ukrainian', label: 'Ukrainian' },
  { value: 'Polish', label: 'Polish' },
  { value: 'Czech', label: 'Czech' },
  { value: 'Thai', label: 'Thai' },
  { value: 'Indonesian', label: 'Indonesian' },
  { value: 'Vietnamese', label: 'Vietnamese' },
  { value: 'Dutch', label: 'Dutch' },
  { value: 'Bengali', label: 'Bengali' },
  { value: 'Turkish', label: 'Turkish' },
  { value: 'Greek', label: 'Greek' },
  { value: 'Swedish', label: 'Swedish' },
  { value: 'Romanian', label: 'Romanian' },
  { value: 'Hungarian', label: 'Hungarian' },
  { value: 'Finnish', label: 'Finnish' },
  { value: 'Norwegian', label: 'Norwegian' },
  { value: 'Danish', label: 'Danish' },
  { value: 'Hebrew', label: 'Hebrew' },
  { value: 'Lithuanian', label: 'Lithuanian' },
  { value: 'Punjabi', label: 'Punjabi' },
  { value: 'Marathi', label: 'Marathi' },
  { value: 'Gujarati', label: 'Gujarati' },
  { value: 'Bhojpuri', label: 'Bhojpuri' },
  { value: 'Nepali', label: 'Nepali' },
  { value: 'Urdu', label: 'Urdu' },
  { value: 'Tagalog', label: 'Tagalog' },
  { value: 'Filipino', label: 'Filipino' },
  { value: 'Malay', label: 'Malay' },
  { value: 'Mongolian', label: 'Mongolian' },
  { value: 'Armenian', label: 'Armenian' },
  { value: 'Georgian', label: 'Georgian' },
] as const

export const SPORTS_CATEGORY_OPTIONS = [
  { value: 'football', label: 'Football' },
  { value: 'american_football', label: 'American Football' },
  { value: 'basketball', label: 'Basketball' },
  { value: 'baseball', label: 'Baseball' },
  { value: 'hockey', label: 'Hockey' },
  { value: 'cricket', label: 'Cricket' },
  { value: 'rugby', label: 'Rugby' },
  { value: 'tennis', label: 'Tennis' },
  { value: 'golf', label: 'Golf' },
  { value: 'mma', label: 'MMA / UFC' },
  { value: 'boxing', label: 'Boxing' },
  { value: 'wrestling', label: 'Wrestling' },
  { value: 'motorsport', label: 'Motorsport' },
  { value: 'other', label: 'Other Sports' },
] as const

export type ContentType = (typeof CONTENT_TYPE_OPTIONS)[number]['value']
export type SportsCategory = (typeof SPORTS_CATEGORY_OPTIONS)[number]['value']
export type ResolutionValue = (typeof RESOLUTION_OPTIONS)[number]['value']
export type QualityValue = (typeof QUALITY_OPTIONS)[number]['value']
export type CodecValue = (typeof CODEC_OPTIONS)[number]['value']
export type AudioValue = (typeof AUDIO_OPTIONS)[number]['value']
export type HDRValue = (typeof HDR_OPTIONS)[number]['value']
export type LanguageValue = (typeof LANGUAGE_OPTIONS)[number]['value']
