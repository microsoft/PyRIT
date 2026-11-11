const SCENARIO_RESULT_ID_QUERY_KEY = 'scenarioResultId'
const FINDING_EVIDENCE_ID_QUERY_KEY = 'findingEvidenceId'
export const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/**
 * Returns the original value represented by a React Router path parameter.
 *
 * React Router already decodes each path segment, but re-escapes decoded
 * slashes as `%2F` so they remain inside one parameter. Undo only that
 * re-escaping; calling `decodeURIComponent` again would corrupt literal `%`
 * sequences and can throw for malformed user-entered URLs.
 */
export function routerPathParamValue(value: string | undefined): string {
  return (value ?? '').replace(/%2F/gi, '/')
}

/** Returns one validated scenario-run provenance UUID from a route query. */
export function scenarioRunProvenance(searchParams: URLSearchParams): string | null {
  const values = searchParams.getAll(SCENARIO_RESULT_ID_QUERY_KEY)
  const first = values[0]
  if (values.length !== 1 || !first || !UUID_PATTERN.test(first)) {
    return null
  }

  return first
}

/** Returns one validated evidence id. Never returns a caller-supplied return URL. */
export function findingEvidenceOrigin(searchParams: URLSearchParams): string | null {
  const values = searchParams.getAll(FINDING_EVIDENCE_ID_QUERY_KEY)
  const first = values[0]
  return values.length === 1 && first && UUID_PATTERN.test(first) ? first : null
}

/** Builds an attack-detail route with optional bounded scenario-run provenance. */
export function attackRoutePath(
  attackResultId: string,
  scenarioResultId?: string | null,
): string {
  return appendScenarioRunProvenance(
    `/attack/chat/${encodeURIComponent(attackResultId)}`,
    scenarioResultId,
  )
}

/** Builds an attack-conversation route with optional bounded scenario-run provenance. */
export function attackConversationRoutePath(
  attackResultId: string,
  conversationId: string,
  scenarioResultId?: string | null,
  findingEvidenceId?: string | null,
): string {
  const path = appendScenarioRunProvenance(
    `/attack/chat/${encodeURIComponent(attackResultId)}/conversations/${encodeURIComponent(conversationId)}`,
    scenarioResultId,
  )
  if (!findingEvidenceId || !UUID_PATTERN.test(findingEvidenceId)) return path
  return `${path}${path.includes('?') ? '&' : '?'}${FINDING_EVIDENCE_ID_QUERY_KEY}=${encodeURIComponent(findingEvidenceId)}`
}

/** Builds the route for one scenario run. Callers must pass a trusted persisted ID. */
export function scenarioRunRoutePath(scenarioResultId: string): string {
  return `/scanner/history/${encodeURIComponent(scenarioResultId)}`
}

/** Builds the route for one attack result within a scenario run. */
export function scenarioRunAttackRoutePath(scenarioResultId: string, attackResultId: string): string {
  return `${scenarioRunRoutePath(scenarioResultId)}/${encodeURIComponent(attackResultId)}`
}

export function scenarioConversationRoutePath(scenarioResultId: string, conversationId: string): string {
  return `${scenarioRunRoutePath(scenarioResultId)}/conversations/${encodeURIComponent(conversationId)}`
}

export function scenarioRerunRoutePath(scenarioName: string, scenarioResultId: string): string {
  return `/scanner/launch/${encodeURIComponent(scenarioName)}?rerun=${encodeURIComponent(scenarioResultId)}`
}

/** Names a stored dataset that the Scanner launch form preselects. Repeatable. */
export const DATASET_QUERY_PARAM = 'dataset'

/** Opens the Scanner launch form for a scenario with stored datasets preselected. */
export function scenarioLaunchRoutePath(scenarioName: string, datasetNames: readonly string[] = []): string {
  const path = `/scanner/launch/${encodeURIComponent(scenarioName)}`
  if (datasetNames.length === 0) return path
  const params = new URLSearchParams()
  for (const name of datasetNames) params.append(DATASET_QUERY_PARAM, name)
  return `${path}?${params.toString()}`
}

/**
 * Reads the distinct dataset names requested for preselection, exactly as written. Names are
 * opaque, so whitespace is kept; blank values are kept too so the form can reject them.
 */
export function datasetNamesFromSearchParams(params: URLSearchParams): string[] {
  return [...new Set(params.getAll(DATASET_QUERY_PARAM))]
}

/** Whether a linked dataset name is blank, which no dataset can be named. */
export function isBlankDatasetName(name: string): boolean {
  return name.trim() === ''
}

function appendScenarioRunProvenance(path: string, scenarioResultId?: string | null): string {
  if (!scenarioResultId || !UUID_PATTERN.test(scenarioResultId)) {
    return path
  }
  const searchParams = new URLSearchParams({
    [SCENARIO_RESULT_ID_QUERY_KEY]: scenarioResultId,
  })
  return `${path}?${searchParams.toString()}`
}

/** Catalog route. Dataset identity is never a path segment. */
export const DATASETS_PATH = '/datasets'

/** Selection route. The opaque key stays in the query and is sent back to the seed API. */
export const DATASET_DETAIL_PATH = '/datasets/detail'

const SELECTION_KEY_PARAM = 'selection_key'

/**
 * Reads the opaque selection key. A missing or repeated key is not a selection
 * and must not be replaced with another dataset. Callers send this value back
 * on seed list and detail requests; they do not look up an arbitrary dataset.
 */
export function selectionKeyFromSearchParams(params: URLSearchParams): string | null {
  const values = params.getAll(SELECTION_KEY_PARAM)
  if (values.length !== 1) return null
  return values[0] ?? null
}

/**
 * Builds `/datasets/detail?selection_key=` with URLSearchParams.
 * The key is never interpolated into the path.
 */
export function datasetDetailPath(selectionKey: string): string {
  const params = new URLSearchParams()
  params.set(SELECTION_KEY_PARAM, selectionKey)
  return `${DATASET_DETAIL_PATH}?${params.toString()}`
}

const NAMED_DATASET_SELECTION_PREFIX = 'dataset:named:'

/** The selection key the dataset catalog assigns to a named dataset. */
export function namedDatasetSelectionKey(datasetName: string): string {
  return `${NAMED_DATASET_SELECTION_PREFIX}${datasetName}`
}

/** Whether a catalog selection key names a dataset, rather than the unnamed seeds. */
export function isNamedDatasetSelectionKey(selectionKey: string): boolean {
  return selectionKey.startsWith(NAMED_DATASET_SELECTION_PREFIX)
    && selectionKey.length > NAMED_DATASET_SELECTION_PREFIX.length
}

const CATALOG_PATH_STATE_KEY = 'catalogPath'

/**
 * Reads a same-app catalog return path from navigation state.
 * Anything that is not exactly `/datasets` plus an optional query falls back
 * to the catalog root so this value cannot be used as an open redirect.
 */
export function catalogPathFromNavigationState(state: unknown): string {
  if (state == null || typeof state !== 'object' || !(CATALOG_PATH_STATE_KEY in state)) {
    return DATASETS_PATH
  }
  const candidate = (state as { catalogPath?: unknown }).catalogPath
  if (typeof candidate !== 'string' || !isCatalogReturnPath(candidate)) {
    return DATASETS_PATH
  }
  return candidate
}

function isCatalogReturnPath(value: string): boolean {
  if (value !== DATASETS_PATH && !value.startsWith(`${DATASETS_PATH}?`)) return false
  if (value.includes('\\') || value.includes('#') || value.includes('://')) return false
  for (const character of value) {
    const code = character.charCodeAt(0)
    if (code <= 31 || code === 127) return false
  }
  let url: URL
  try {
    url = new URL(value, 'http://pyrit.local')
  } catch {
    return false
  }
  return url.origin === 'http://pyrit.local'
    && url.username === ''
    && url.password === ''
    && url.pathname === DATASETS_PATH
    && url.hash === ''
}

/** Returns true if the pathname or path belongs to the dataset catalog or dataset detail view. */
export function isDatasetsPath(pathOrUrl: string): boolean {
  if (!pathOrUrl) return false
  const cleanPath = pathOrUrl.split('?')[0]?.split('#')[0] ?? ''
  return cleanPath === DATASETS_PATH || cleanPath === DATASET_DETAIL_PATH || cleanPath.startsWith(`${DATASETS_PATH}/`)
}

/** Returns true if the pathname or path belongs to the chat view or an attack view. */
export function isChatPath(pathOrUrl: string): boolean {
  if (!pathOrUrl) return false
  const cleanPath = pathOrUrl.split('?')[0]?.split('#')[0] ?? ''
  return cleanPath === '/attack/chat' || cleanPath.startsWith('/attack/chat/')
}

/** Parses attackId and conversationId from a chat or attack route path. */
export function parseChatRoute(path: string): { attackId: string | null; conversationId: string | null } {
  if (!path) return { attackId: null, conversationId: null }
  const clean = path.split('?')[0]?.split('#')[0] ?? ''
  const matchWithConv = /^\/attacks\/([^/]+)\/conversations\/([^/]+)/.exec(clean)
  if (matchWithConv) {
    const rawAttackId = matchWithConv[1] ?? ''
    const rawConversationId = matchWithConv[2] ?? ''
    try {
      return {
        attackId: decodeURIComponent(rawAttackId),
        conversationId: decodeURIComponent(rawConversationId),
      }
    } catch {
      return { attackId: rawAttackId, conversationId: rawConversationId }
    }
  }
  const matchAttack = /^\/attacks\/([^/]+)/.exec(clean)
  if (matchAttack) {
    const rawAttackId = matchAttack[1] ?? ''
    try {
      return {
        attackId: decodeURIComponent(rawAttackId),
        conversationId: null,
      }
    } catch {
      return { attackId: rawAttackId, conversationId: null }
    }
  }
  return { attackId: null, conversationId: null }
}
