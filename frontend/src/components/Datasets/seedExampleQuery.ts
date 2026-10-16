import type { SeedType } from '@/types'
import { catalogPathFromNavigationState } from '@/utils/routeParams'

export const MAX_SEED_SEARCH_LENGTH = 1_000
export const DEFAULT_SEED_PAGE_SIZE = 20
export const MIN_SEED_PAGE_SIZE = 1
export const MAX_SEED_PAGE_SIZE = 100
export const SEED_PAGE_SIZE_OPTIONS = [20, 50, 100] as const

export const SEED_TYPES = ['prompt', 'objective', 'simulated_conversation'] as const

export const SEED_TYPE_LABELS: Record<SeedType, string> = {
  prompt: 'Prompt',
  objective: 'Objective',
  simulated_conversation: 'Simulated conversation',
}

const SEARCH_PARAM = 'search'
const MODALITY_PARAM = 'modality'
const SEED_TYPE_PARAM = 'seed_type'
const HARM_CATEGORY_PARAM = 'harm_category'
const LIMIT_PARAM = 'limit'
const CURSOR_PARAM = 'cursor'
const EXAMPLE_PARAM = 'example'
const CURSOR_GENERATION_KEY = 'cursorGeneration'

export interface SeedBrowserQuery {
  readonly search: string
  readonly modalities: readonly string[]
  readonly seedTypes: readonly SeedType[]
  readonly harmCategories: readonly string[]
  readonly limit: number
  readonly cursor: string | null
  readonly exampleId: string | null
  readonly cursorRepeated: boolean
  readonly exampleRepeated: boolean
}

export interface DatasetDetailLocationState {
  readonly catalogPath: string
  readonly seedCursors: readonly (string | null)[]
  readonly cursorGeneration: string
}

export function isSeedType(value: string): value is SeedType {
  return SEED_TYPES.some((seedType) => seedType === value)
}

function listed(values: readonly string[]): string[] {
  const seen = new Set<string>()
  const unique: string[] = []
  for (const value of values) {
    if (!value || seen.has(value)) continue
    seen.add(value)
    unique.push(value)
  }
  return unique
}

function oneValue(params: URLSearchParams, key: string): { value: string | null; repeated: boolean } {
  const values = params.getAll(key)
  if (values.length > 1) return { value: null, repeated: true }
  if (values.length === 0 || values[0] === '') return { value: null, repeated: false }
  return { value: values[0], repeated: false }
}

function pageSize(params: URLSearchParams): number {
  const values = params.getAll(LIMIT_PARAM)
  if (values.length !== 1) return DEFAULT_SEED_PAGE_SIZE
  const parsed = Number(values[0])
  if (!Number.isInteger(parsed) || parsed < MIN_SEED_PAGE_SIZE || parsed > MAX_SEED_PAGE_SIZE) {
    return DEFAULT_SEED_PAGE_SIZE
  }
  return parsed
}

function seedTypesFrom(params: URLSearchParams): SeedType[] {
  const seedTypes: SeedType[] = []
  for (const value of params.getAll(SEED_TYPE_PARAM)) {
    if (!isSeedType(value) || seedTypes.includes(value)) continue
    seedTypes.push(value)
  }
  return seedTypes
}

/**
 * Reads example-browser state from the detail URL.
 * Catalog `q`, `unlabeled`, and `sort` are ignored. A repeated cursor or example
 * is not resolved by picking one of the values.
 */
export function seedBrowserQueryFromSearchParams(params: URLSearchParams): SeedBrowserQuery {
  const searchValues = params.getAll(SEARCH_PARAM)
  const search = searchValues.length === 1 ? searchValues[0].slice(0, MAX_SEED_SEARCH_LENGTH) : ''
  const cursor = oneValue(params, CURSOR_PARAM)
  const example = oneValue(params, EXAMPLE_PARAM)
  return {
    search,
    modalities: listed(params.getAll(MODALITY_PARAM)),
    seedTypes: seedTypesFrom(params),
    harmCategories: listed(params.getAll(HARM_CATEGORY_PARAM)),
    limit: pageSize(params),
    cursor: cursor.repeated ? null : cursor.value,
    exampleId: example.repeated ? null : example.value,
    cursorRepeated: cursor.repeated,
    exampleRepeated: example.repeated,
  }
}

/** Writes example-browser state. Catalog name search, unlabeled, and sort are not included. */
export function seedBrowserSearchParams(selectionKey: string, query: SeedBrowserQuery): URLSearchParams {
  const params = new URLSearchParams()
  params.set('selection_key', selectionKey)
  if (query.search) params.set(SEARCH_PARAM, query.search.slice(0, MAX_SEED_SEARCH_LENGTH))
  for (const modality of query.modalities) params.append(MODALITY_PARAM, modality)
  for (const seedType of query.seedTypes) params.append(SEED_TYPE_PARAM, seedType)
  for (const category of query.harmCategories) params.append(HARM_CATEGORY_PARAM, category)
  if (query.limit !== DEFAULT_SEED_PAGE_SIZE) params.set(LIMIT_PARAM, String(query.limit))
  if (query.cursor && !query.cursorRepeated) params.set(CURSOR_PARAM, query.cursor)
  if (query.exampleId && !query.exampleRepeated) params.set(EXAMPLE_PARAM, query.exampleId)
  return params
}

export function seedFiltersActive(query: SeedBrowserQuery): boolean {
  return query.search !== ''
    || query.modalities.length > 0
    || query.seedTypes.length > 0
    || query.harmCategories.length > 0
}

export function clearedSeedFilters(query: SeedBrowserQuery): SeedBrowserQuery {
  return {
    ...query,
    search: '',
    modalities: [],
    seedTypes: [],
    harmCategories: [],
    cursor: null,
    cursorRepeated: false,
  }
}

export interface RuntimeGeneration {
  readonly generation: string
  readonly ready: boolean
}

/** A generation can reset paging only after the runtime is ready and has reported an id. */
export function isKnownRuntimeGeneration(runtime: RuntimeGeneration): boolean {
  return runtime.ready && runtime.generation !== ''
}

/**
 * True only for a transition between two known generations.
 * An empty id or a runtime that is not ready is unknown, so it is not a change.
 */
export function runtimeGenerationChanged(previous: RuntimeGeneration, next: RuntimeGeneration): boolean {
  return isKnownRuntimeGeneration(previous)
    && isKnownRuntimeGeneration(next)
    && previous.generation !== next.generation
}

/** Drops the page cursor and the open example. Filters and page size stay. */
export function seedQueryWithoutPaging(query: SeedBrowserQuery): SeedBrowserQuery {
  return {
    ...query,
    cursor: null,
    exampleId: null,
    cursorRepeated: false,
    exampleRepeated: false,
  }
}

/**
 * True when saved navigation state names a different known runtime than the one on screen.
 * An unknown current or recorded generation does not make the saved page stale.
 */
export function seedCursorIsStale(state: unknown, generation: string, ready = true): boolean {
  if (!isKnownRuntimeGeneration({ generation, ready })) return false
  if (state == null || typeof state !== 'object' || !(CURSOR_GENERATION_KEY in state)) return false
  const recorded = (state as { cursorGeneration?: unknown }).cursorGeneration
  return typeof recorded === 'string' && recorded !== '' && recorded !== generation
}

export function seedCursorsFromNavigationState(state: unknown): readonly (string | null)[] {
  if (state == null || typeof state !== 'object' || !('seedCursors' in state)) return []
  const value = (state as { seedCursors?: unknown }).seedCursors
  if (!Array.isArray(value)) return []
  const cursors: Array<string | null> = []
  for (const entry of value) {
    if (entry === null) {
      cursors.push(null)
      continue
    }
    if (typeof entry !== 'string' || entry.length === 0) return []
    cursors.push(entry)
  }
  return cursors
}

export function seedNavigationState(
  current: unknown,
  seedCursors: readonly (string | null)[],
  generation: string,
): DatasetDetailLocationState {
  return {
    catalogPath: catalogPathFromNavigationState(current),
    seedCursors: [...seedCursors],
    cursorGeneration: generation,
  }
}

export function pushSeedCursor(
  cursors: readonly (string | null)[],
  currentCursor: string | null,
): readonly (string | null)[] {
  return [...cursors, currentCursor]
}

export function popSeedCursor(cursors: readonly (string | null)[]): {
  readonly cursor: string | null
  readonly seedCursors: readonly (string | null)[]
} | null {
  if (cursors.length === 0) return null
  return {
    cursor: cursors[cursors.length - 1],
    seedCursors: cursors.slice(0, -1),
  }
}

/** Page number within this visit. A cursor without visited history has no number. */
export function seedPageNumber(query: SeedBrowserQuery, cursors: readonly (string | null)[]): number | null {
  if (query.cursorRepeated) return null
  if (query.cursor && cursors.length === 0) return null
  return cursors.length + 1
}
