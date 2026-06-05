export const PIECE_TYPE_TO_DATA_TYPE: Record<string, string> = {
  text: 'text',
  image: 'image_path',
  audio: 'audio_path',
  video: 'video_path',
  file: 'binary_path',
}

export interface PieceConversion {
  converterInstanceId: string
  convertedValue: string
  originalValue: string
  /** Input piece type the conversion came from (e.g. 'text', 'image'). */
  pieceType: string
  /**
   * Backend data type of the converted value (e.g. 'text', 'image_path',
   * 'binary_path'). May differ from the input piece type when a converter
   * changes the data type — e.g. PDFConverter takes text and emits binary_path.
   */
  convertedDataType: string
}

/**
 * True when the converter's output is a file path served via /api/media,
 * i.e. anything that ends with `_path` (image, audio, video, binary).
 */
export function isPathDataType(dataType: string | undefined | null): boolean {
  return typeof dataType === 'string' && dataType.endsWith('_path')
}

/**
 * Map a backend data type to the corresponding frontend MessageAttachment type.
 */
export function dataTypeToAttachmentKind(dataType: string): 'image' | 'audio' | 'video' | 'file' {
  if (dataType.startsWith('image')) return 'image'
  if (dataType.startsWith('audio')) return 'audio'
  if (dataType.startsWith('video')) return 'video'
  return 'file'
}

/**
 * Build a /api/media URL for a stored file path. Pass-through when the value
 * is already a URL or data URI.
 */
export function buildMediaUrl(value: string): string {
  if (value.startsWith('http://') || value.startsWith('https://') || value.startsWith('data:')) {
    return value
  }
  if (value.startsWith('/api/media')) return value
  return `/api/media?path=${encodeURIComponent(value)}`
}

/**
 * Extract a display filename from a path/URL; falls back to a sensible default.
 */
export function basenameFromValue(value: string, fallback: string): string {
  if (!value) return fallback
  // Handle /api/media?path=... form
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
