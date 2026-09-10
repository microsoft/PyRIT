/** A single cursor-paginated response page, as returned by the backend's list endpoints. */
export interface CursorPage<T> {
  items: T[]
  pagination: {
    has_more: boolean
    next_cursor?: string | null
  }
}

/** Hard cap on pagination loops so a misbehaving/cyclic cursor can't hang the fetch. */
const DEFAULT_MAX_PAGES = 50

/**
 * Fetches every page of a cursor-paginated list endpoint, following
 * `pagination.next_cursor` until `has_more` is false.
 *
 * Guards against a server bug that repeats the same cursor (which would
 * otherwise loop forever) by stopping as soon as a cursor is seen twice, and
 * against an unbounded loop via `maxPages`.
 */
export async function fetchAllPages<T>(
  fetchPage: (cursor: string | undefined) => Promise<CursorPage<T>>,
  maxPages: number = DEFAULT_MAX_PAGES,
  getKey?: (item: T) => string,
): Promise<T[]> {
  const items: T[] = []
  let cursor: string | undefined
  const seenCursors = new Set<string>()
  const seenItemKeys = new Set<string>()

  for (let page = 0; page < maxPages; page++) {
    const response = await fetchPage(cursor)
    for (const item of response.items) {
      if (!getKey) {
        items.push(item)
        continue
      }
      const key = getKey(item)
      if (!seenItemKeys.has(key)) {
        seenItemKeys.add(key)
        items.push(item)
      }
    }
    if (!response.pagination.has_more || !response.pagination.next_cursor) {
      break
    }
    const nextCursor = response.pagination.next_cursor
    if (seenCursors.has(nextCursor)) {
      break
    }
    seenCursors.add(nextCursor)
    cursor = nextCursor
  }

  return items
}
