import { scorersApi } from '@/services/api'
import type { ScorerInstance } from '@/types'

export async function listRegisteredScorers(): Promise<ScorerInstance[]> {
  const scorers = new Map<string, ScorerInstance>()
  const cursors = new Set<string>()
  let cursor: string | undefined
  for (let page = 0; page < 100; page++) {
    const response = await scorersApi.listScorers(200, cursor)
    for (const scorer of response.items) scorers.set(scorer.scorer_registry_name, scorer)
    if (!response.pagination.has_more) return [...scorers.values()]
    const next = response.pagination.next_cursor
    if (!next || cursors.has(next)) throw new Error('Scorer registry pagination did not advance.')
    cursors.add(next)
    cursor = next
  }
  throw new Error('Scorer registry pagination exceeded the page limit.')
}
