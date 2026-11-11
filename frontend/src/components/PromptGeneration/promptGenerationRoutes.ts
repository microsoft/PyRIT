/**
 * Prompt generation lives under the dataset catalog because every generation
 * saves a dataset. The static `new` segment ranks above the `:generationId`
 * parameter, and generation IDs are server-assigned, so no generation can shadow it.
 */

export const PROMPT_GENERATIONS_PATH = '/datasets/generate'
export const NEW_PROMPT_GENERATION_PATH = `${PROMPT_GENERATIONS_PATH}/new`
export const PROMPT_GENERATION_ROUTE = `${PROMPT_GENERATIONS_PATH}/:generationId`

const GENERATOR_PARAM = 'generator'
const TEMPLATE_PARAM = 'template'
const SUITE_PARAM = 'suite'
const FROM_PARAM = 'from'
const SELECTION_KEY_PARAM = 'selection_key'

/** Where the new-generation form takes its initial values from. */
export interface NewGenerationParams {
  readonly generator?: string
  /** A generator-defined template, such as an ASSERT library behavior. */
  readonly template?: string
  /** A generator-defined template for several datasets, such as an ASSERT suite. */
  readonly suite?: string
  /** An existing generation whose inputs pre-fill the form. */
  readonly from?: string
  /** A stored dataset whose generator request pre-fills the form. */
  readonly selectionKey?: string
}

export function promptGenerationRoutePath(generationId: string): string {
  return `${PROMPT_GENERATIONS_PATH}/${encodeURIComponent(generationId)}`
}

export function newPromptGenerationRoutePath(params: NewGenerationParams = {}): string {
  const query = new URLSearchParams()
  if (params.generator) query.set(GENERATOR_PARAM, params.generator)
  if (params.template) query.set(TEMPLATE_PARAM, params.template)
  if (params.suite) query.set(SUITE_PARAM, params.suite)
  if (params.from) query.set(FROM_PARAM, params.from)
  if (params.selectionKey) query.set(SELECTION_KEY_PARAM, params.selectionKey)
  const search = query.toString()
  return search ? `${NEW_PROMPT_GENERATION_PATH}?${search}` : NEW_PROMPT_GENERATION_PATH
}

function singleValue(params: URLSearchParams, name: string): string | undefined {
  const values = params.getAll(name)
  const value = values.length === 1 ? values[0]?.trim() : undefined
  return value || undefined
}

/** Reads one opaque value exactly as written, since whitespace can be part of a dataset's identity. */
function exactValue(params: URLSearchParams, name: string): string | undefined {
  const values = params.getAll(name)
  const value = values.length === 1 ? values[0] : undefined
  return value?.trim() ? value : undefined
}

/** Reads the new-generation query. A missing or repeated parameter is ignored. */
export function newGenerationParamsFromSearch(params: URLSearchParams): NewGenerationParams {
  return {
    generator: singleValue(params, GENERATOR_PARAM),
    template: singleValue(params, TEMPLATE_PARAM),
    suite: singleValue(params, SUITE_PARAM),
    from: singleValue(params, FROM_PARAM),
    selectionKey: exactValue(params, SELECTION_KEY_PARAM),
  }
}
