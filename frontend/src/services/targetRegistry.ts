import { targetsApi } from '@/services/api'
import type { TargetInstance } from '@/types'

const TARGET_PAGE_SIZE = 200
const TARGET_MAX_PAGES = 100

export async function buildTemperatureTarget(target: TargetInstance, temperature: number): Promise<TargetInstance> {
  if (!Number.isFinite(temperature) || temperature < 0 || temperature > 2) {
    throw new Error('Temperature must be between 0 and 2.')
  }
  if (!target.supports_temperature_override) {
    throw new Error(target.reconstruction_error ?? 'This target does not support a separate temperature setting.')
  }
  const sourceName = target.binding?.source_name ?? target.target_registry_name
  const sourceHash = target.binding?.source_hash ?? target.identifier.hash
  const built = await targetsApi.buildTarget(target.identifier.class_name, {
    source_name: sourceName, source_hash: sourceHash, params: { temperature },
  })
  return {
    ...target, ...built,
    binding: {
      version: 1, source_name: sourceName, source_hash: sourceHash,
      temperature, effective_hash: built.identifier.hash,
    },
  }
}

/** Target identity resolution requires the complete registry, not a partial page set. */
export async function listRegisteredTargets(): Promise<TargetInstance[]> {
  const targets = new Map<string, TargetInstance>()
  const seenCursors = new Set<string>()
  let cursor: string | undefined

  for (let page = 0; page < TARGET_MAX_PAGES; page++) {
    const response = await targetsApi.listTargets(TARGET_PAGE_SIZE, cursor)
    for (const target of response.items) targets.set(target.target_registry_name, target)
    if (!response.pagination.has_more) return [...targets.values()]
    const nextCursor = response.pagination.next_cursor
    if (!nextCursor || seenCursors.has(nextCursor)) {
      throw new Error('Target registry pagination did not advance')
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  }
  throw new Error('Target registry pagination exceeded the page limit')
}
