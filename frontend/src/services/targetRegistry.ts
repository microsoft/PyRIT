import { targetsApi } from '@/services/api'
import type { TargetInstance } from '@/types'
import { isGuiTarget } from '@/utils/targetVisibility'

const TARGET_PAGE_SIZE = 200
const TARGET_MAX_PAGES = 100

/** Load every registry page before excluding SDK-only targets from GUI choices. */
export async function listRegisteredTargets(): Promise<TargetInstance[]> {
  const targets = new Map<string, TargetInstance>()
  const seenCursors = new Set<string>()
  let cursor: string | undefined

  for (let page = 0; page < TARGET_MAX_PAGES; page++) {
    const response = await targetsApi.listTargets(TARGET_PAGE_SIZE, cursor)
    for (const target of response.items) targets.set(target.target_registry_name, target)
    if (!response.pagination.has_more) return [...targets.values()].filter(isGuiTarget)
    const nextCursor = response.pagination.next_cursor
    if (!nextCursor || seenCursors.has(nextCursor)) {
      throw new Error('Target registry pagination did not advance')
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  }
  throw new Error('Target registry pagination exceeded the page limit')
}
