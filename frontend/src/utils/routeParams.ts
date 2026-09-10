const SCENARIO_RESULT_ID_QUERY_KEY = 'scenarioResultId'
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

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
  if (values.length !== 1 || !UUID_PATTERN.test(values[0])) {
    return null
  }
  return values[0]
}

/** Builds an attack-detail route with optional bounded scenario-run provenance. */
export function attackRoutePath(
  attackResultId: string,
  scenarioResultId?: string | null,
): string {
  return appendScenarioRunProvenance(
    `/attacks/${encodeURIComponent(attackResultId)}`,
    scenarioResultId,
  )
}

/** Builds an attack-conversation route with optional bounded scenario-run provenance. */
export function attackConversationRoutePath(
  attackResultId: string,
  conversationId: string,
  scenarioResultId?: string | null,
): string {
  return appendScenarioRunProvenance(
    `/attacks/${encodeURIComponent(attackResultId)}/conversations/${encodeURIComponent(conversationId)}`,
    scenarioResultId,
  )
}

/** Builds the route for one scenario run. Callers must pass a trusted persisted ID. */
export function scenarioRunRoutePath(scenarioResultId: string): string {
  return `/scanner-history/${encodeURIComponent(scenarioResultId)}`
}

/** Builds the route for one attack result within a scenario run. */
export function scenarioRunAttackRoutePath(scenarioResultId: string, attackResultId: string): string {
  return `${scenarioRunRoutePath(scenarioResultId)}/${encodeURIComponent(attackResultId)}`
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
