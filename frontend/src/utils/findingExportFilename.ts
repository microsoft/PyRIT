import type { Operation } from '@/types'

const NAME_LIMIT = 60
const MARK = /^\p{M}$/u

/**
 * Name an exported findings document after its Operation and the user's local time, e.g.
 * `copyrit-findings-synthetic-evaluation-2026-10-22-1258.docx`. The backend's suggested name, for
 * other clients, uses the Operation ID and labelled UTC instead.
 */
export function findingExportFilename(operation: Pick<Operation, 'id' | 'name'>, extension: string, now: Date): string {
  const name = Array.from(
    operation.name.normalize('NFC').toLowerCase().replace(/[^\p{L}\p{M}\p{N}]+/gu, '-').replace(/^-+|-+$/g, ''),
  )
  let end = name.length
  if (name.length > NAME_LIMIT) {
    end = NAME_LIMIT
    if (name[end] !== '-' && name.slice(0, end).includes('-')) end = name.lastIndexOf('-', end - 1)
    // A word longer than the limit is cut between letters, never between a letter and its marks.
    while (end > 0 && MARK.test(name[end])) end -= 1
  }
  const slug = name.slice(0, end).join('').replace(/-+$/, '')
  const pad = (value: number) => String(value).padStart(2, '0')
  const date = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  return `copyrit-findings-${slug || operation.id}-${date}-${pad(now.getHours())}${pad(now.getMinutes())}.${extension}`
}
