export function isPathDataType(dataType: string | undefined | null): boolean {
  return typeof dataType === 'string' && dataType.endsWith('_path')
}

export function dataTypeToAttachmentKind(dataType: string): 'image' | 'audio' | 'video' | 'file' {
  if (dataType.startsWith('image')) return 'image'
  if (dataType.startsWith('audio')) return 'audio'
  if (dataType.startsWith('video')) return 'video'
  return 'file'
}

export function buildMediaUrl(value: string): string {
  if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('data:')) {
    return value
  }
  if (value.startsWith('/api/media')) return value
  return `/api/media?path=${encodeURIComponent(value)}`
}

export function basenameFromValue(value: string, fallback: string): string {
  if (!value) return fallback
  if (value.startsWith('/api/media')) {
    const match = /[?&]path=([^&]+)/.exec(value)
    if (match) {
      try {
        const decoded = decodeURIComponent(match[1])
        const parts = decoded.split(/[/\\]/)
        return parts[parts.length - 1] || fallback
      } catch {
        return fallback
      }
    }
    return fallback
  }
  const cleaned = value.split('?')[0]
  const parts = cleaned.split(/[/\\]/)
  return parts[parts.length - 1] || fallback
}

/** Kinds the shared preview can render. Active formats such as SVG and HTML are never included. */
export type MediaPreviewKind = 'image' | 'audio' | 'video'

/**
 * Result of mapping a stored media reference to something the browser may fetch.
 * `preview` is safe to pass to the shared player. `unsupported` is download-only.
 * `missing` means there is no client-fetchable URL. `none` is not media.
 */
export type MappedMedia =
  | { status: 'preview'; kind: MediaPreviewKind; url: string }
  | { status: 'unsupported'; url: string | null }
  | { status: 'missing' }
  | { status: 'none' }

export interface MapMediaUrlInput {
  readonly dataType: string
  readonly value: string
  /** Display URL from the backend. A non-URL here means the file is missing. */
  readonly valueUrl?: string | null
}

// Same inline allowlist as pyrit/backend/routes/media.py. SVG and HTML stay out.
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.ico', '.tiff'])
const AUDIO_EXTENSIONS = new Set(['.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'])
const VIDEO_EXTENSIONS = new Set(['.mp4', '.webm', '.mov', '.avi', '.mkv'])
const ACTIVE_EXTENSIONS = new Set(['.svg', '.svgz', '.html', '.htm', '.xhtml', '.xml', '.js', '.mjs'])

const IMAGE_MIMES = new Set([
  'image/png',
  'image/jpeg',
  'image/gif',
  'image/bmp',
  'image/webp',
  'image/tiff',
  'image/x-icon',
  'image/vnd.microsoft.icon',
])
const AUDIO_MIMES = new Set([
  'audio/mpeg',
  'audio/wav',
  'audio/wave',
  'audio/x-wav',
  'audio/ogg',
  'audio/flac',
  'audio/aac',
  'audio/mp4',
  'audio/x-m4a',
])
const VIDEO_MIMES = new Set([
  'video/mp4',
  'video/webm',
  'video/quicktime',
  'video/x-msvideo',
  'video/x-matroska',
])

const PREVIEW_DATA_TYPES: Readonly<Record<string, MediaPreviewKind>> = {
  image_path: 'image',
  audio_path: 'audio',
  video_path: 'video',
}

type ExtensionKind = MediaPreviewKind | 'unsupported' | 'unknown'

function isFetchableMediaUrl(value: string): boolean {
  return value.startsWith('https://')
    || value.startsWith('http://')
    || value.startsWith('data:')
    || value.startsWith('/api/media')
    || value.startsWith('blob:')
}

function referencePath(url: string): string {
  if (url.startsWith('/api/media')) {
    const queryIndex = url.indexOf('?')
    const params = new URLSearchParams(queryIndex >= 0 ? url.slice(queryIndex + 1) : '')
    return params.get('path') ?? ''
  }
  if (url.startsWith('data:')) return url
  const hash = url.indexOf('#')
  const withoutHash = hash >= 0 ? url.slice(0, hash) : url
  const query = withoutHash.indexOf('?')
  return query >= 0 ? withoutHash.slice(0, query) : withoutHash
}

function extensionOf(url: string): string {
  if (url.startsWith('data:')) return ''
  const name = referencePath(url).split(/[/\\]/).pop() ?? ''
  const dot = name.lastIndexOf('.')
  if (dot <= 0) return ''
  return name.slice(dot).toLowerCase()
}

function kindForExtension(extension: string): ExtensionKind {
  if (!extension) return 'unknown'
  if (ACTIVE_EXTENSIONS.has(extension)) return 'unsupported'
  if (IMAGE_EXTENSIONS.has(extension)) return 'image'
  if (AUDIO_EXTENSIONS.has(extension)) return 'audio'
  if (VIDEO_EXTENSIONS.has(extension)) return 'video'
  return 'unsupported'
}

function dataUriKind(url: string): MediaPreviewKind | 'unsupported' | null {
  if (!url.startsWith('data:')) return null
  const meta = url.slice('data:'.length).split(',')[0] ?? ''
  const mime = (meta.split(';')[0] ?? '').trim().toLowerCase()
  if (IMAGE_MIMES.has(mime)) return 'image'
  if (AUDIO_MIMES.has(mime)) return 'audio'
  if (VIDEO_MIMES.has(mime)) return 'video'
  // Includes SVG, HTML, and any other data URI. Those must not be rendered.
  return 'unsupported'
}

export interface MediaAccessLink {
  readonly href: string
  readonly label: 'Download' | 'Open in new tab'
  /** Set only when the browser will honor the download attribute. */
  readonly downloadName: string | null
  readonly openInNewTab: boolean
}

/**
 * Same-origin, blob, and data URLs can download in place. A signed blob URL is
 * cross-origin, and browsers ignore `download` there and navigate the current tab.
 */
export function mediaAccessLink(url: string, filename: string, pageOrigin: string): MediaAccessLink {
  let sameDocument = url.startsWith('/') || url.startsWith('blob:') || url.startsWith('data:')
  if (!sameDocument) {
    try {
      const parsed = new URL(url, pageOrigin)
      sameDocument = parsed.protocol === 'blob:'
        || parsed.protocol === 'data:'
        || parsed.origin === pageOrigin
    } catch {
      sameDocument = false
    }
  }
  if (sameDocument) {
    return { href: url, label: 'Download', downloadName: filename, openInNewTab: false }
  }
  return { href: url, label: 'Open in new tab', downloadName: null, openInNewTab: true }
}

function supportedDownloadUrl(url: string): string | null {
  // The media route attaches non-inline types. Anywhere else, refuse active content.
  if (url.startsWith('/api/media')) return url
  const inlineKind = dataUriKind(url) ?? kindForExtension(extensionOf(url))
  if (inlineKind === 'unsupported') return null
  if (url.startsWith('data:') || url.startsWith('https://') || url.startsWith('http://') || url.startsWith('blob:')) {
    return url
  }
  return null
}

/**
 * Map a stored seed or message media reference to one preview URL.
 *
 * Prefers the backend display URL (`valueUrl`) and never turns a bare filesystem
 * path into a browser URL. SVG, HTML, and other active types are not previews.
 */
export function mapMediaUrl({ dataType, value, valueUrl }: MapMediaUrlInput): MappedMedia {
  const previewKind = PREVIEW_DATA_TYPES[dataType]
  const isBinary = dataType === 'binary_path'
  if (!previewKind && !isBinary) return { status: 'none' }

  const explicit = valueUrl?.trim() ?? ''
  const stored = value.trim()
  if (explicit && !isFetchableMediaUrl(explicit)) return { status: 'missing' }

  const url = explicit || (isFetchableMediaUrl(stored) ? stored : '')
  if (!url) return { status: 'missing' }
  if (isBinary) return { status: 'unsupported', url: supportedDownloadUrl(url) }

  const classified = dataUriKind(url) ?? kindForExtension(extensionOf(url))
  if (classified === 'unsupported') return { status: 'unsupported', url: supportedDownloadUrl(url) }
  if (classified !== 'unknown' && classified !== previewKind) {
    return { status: 'unsupported', url: supportedDownloadUrl(url) }
  }
  return { status: 'preview', kind: previewKind, url }
}
