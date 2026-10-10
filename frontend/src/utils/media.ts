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
