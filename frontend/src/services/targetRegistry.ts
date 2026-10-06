import { targetsApi } from '@/services/api'
import type { TargetInstance, UnrestorableInstance } from '@/types'

const TARGET_PAGE_SIZE = 200
const TARGET_MAX_PAGES = 100

/** Every registered target, plus the saved targets the backend could not restore and why. */
export interface TargetRegistrySnapshot {
  targets: TargetInstance[]
  unrestorable: UnrestorableInstance[]
  restoreError: string | null
}

/** Reads the complete registry; target identity resolution cannot rely on a partial page set. */
export async function listTargetRegistry(): Promise<TargetRegistrySnapshot> {
  const targets = new Map<string, TargetInstance>()
  const seenCursors = new Set<string>()
  let cursor: string | undefined

  for (let page = 0; page < TARGET_MAX_PAGES; page++) {
    const response = await targetsApi.listTargets(TARGET_PAGE_SIZE, cursor)
    for (const target of response.items) targets.set(target.target_registry_name, target)
    if (!response.pagination.has_more) {
      return {
        targets: [...targets.values()],
        unrestorable: response.unrestorable ?? [],
        restoreError: response.restore_error ?? null,
      }
    }
    const nextCursor = response.pagination.next_cursor
    if (!nextCursor || seenCursors.has(nextCursor)) {
      throw new Error('Target registry pagination did not advance')
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  }
  throw new Error('Target registry pagination exceeded the page limit')
}

/** Every registered target, read from the complete registry. */
export async function listRegisteredTargets(): Promise<TargetInstance[]> {
  return (await listTargetRegistry()).targets
}
