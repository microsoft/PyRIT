import type {
  AssertDimension,
  AssertGenerationRequest,
  AssertReplication,
  AssertSamplingMethod,
} from '@/types'
import { generateClientId } from '@/utils/clientId'

import {
  asRecord,
  parseWholeNumber,
  readBoolean,
  readNumberText,
  readString,
  readStringList,
} from './generationFormHelpers'
import { datasetNameFromText } from './promptGenerationPresentation'

export type AssertDimensionMode = 'describe' | 'explicit'
export type AssertInstructionMode = 'default' | 'custom' | 'target'

export const ASSERT_SAMPLING_METHODS: readonly AssertSamplingMethod[] = ['pairwise', 'stratified', 'full_factorial', 'random']
export const ASSERT_SAMPLING_LABELS: Record<AssertSamplingMethod, string> = {
  pairwise: 'Pairwise (cover every pair of levels)',
  stratified: 'Stratified',
  full_factorial: 'Full factorial',
  random: 'Random',
}
/** The taxonomy axis; no coverage dimension may use this name. */
export const RESERVED_DIMENSION_NAME = 'behavior'

const DEFAULT_GROUP_COUNT = 25
const DEFAULT_CATEGORY_COUNT = 25
const DEFAULT_LEVEL_COUNT = 3
const MAX_GROUP_COUNT = 100_000
const MAX_CATEGORY_COUNT = 1_000
const MIN_LEVEL_COUNT = 2
const MAX_LEVEL_COUNT = 100

export interface AssertLevelDraft {
  readonly id: string
  readonly name: string
  readonly definition: string
}

export interface AssertDimensionDraft {
  readonly id: string
  readonly name: string
  readonly description: string
  readonly levels: readonly AssertLevelDraft[]
}

/** Settings every ASSERT dataset in a form shares; numbers stay as typed until submit. */
export interface AssertSettingsDraft {
  readonly context: string
  readonly groupCount: string
  readonly includeUserPrompt: boolean
  readonly includeAdversarialPrompt: boolean
  readonly behaviorCategoryCount: string
  readonly dimensionMode: AssertDimensionMode
  readonly dimensions: readonly AssertDimensionDraft[]
  readonly dimensionLevelCount: string
  readonly samplingMethod: AssertSamplingMethod
  readonly stratifyBy: readonly string[]
  readonly replication: AssertReplication
  readonly withReplacement: boolean
  readonly randomSeed: string
  readonly webSearch: boolean
  readonly instructionMode: AssertInstructionMode
  readonly targetSystemPrompt: string
}

export interface AssertBehaviorDraft {
  readonly behaviorName: string
  readonly behaviorDescription: string
}

export function defaultAssertSettings(): AssertSettingsDraft {
  return {
    context: '',
    groupCount: String(DEFAULT_GROUP_COUNT),
    includeUserPrompt: true,
    includeAdversarialPrompt: true,
    behaviorCategoryCount: String(DEFAULT_CATEGORY_COUNT),
    dimensionMode: 'describe',
    dimensions: [],
    dimensionLevelCount: String(DEFAULT_LEVEL_COUNT),
    samplingMethod: 'pairwise',
    stratifyBy: [RESERVED_DIMENSION_NAME],
    replication: 'balanced',
    withReplacement: true,
    randomSeed: '0',
    webSearch: false,
    instructionMode: 'default',
    targetSystemPrompt: '',
  }
}

export function newLevelDraft(): AssertLevelDraft {
  return { id: generateClientId(), name: '', definition: '' }
}

export function newDimensionDraft(mode: AssertDimensionMode): AssertDimensionDraft {
  return {
    id: generateClientId(),
    name: '',
    description: '',
    levels: mode === 'explicit' ? [newLevelDraft(), newLevelDraft()] : [],
  }
}

function readSamplingMethod(value: unknown): AssertSamplingMethod {
  return ASSERT_SAMPLING_METHODS.find((method) => method === value) ?? 'pairwise'
}

function readDimensions(value: unknown): AssertDimensionDraft[] {
  if (!Array.isArray(value)) return []
  return value.map((item) => {
    const dimension = asRecord(item)
    const levels = Array.isArray(dimension.levels) ? dimension.levels : []
    return {
      id: generateClientId(),
      name: readString(dimension.name),
      description: readString(dimension.description),
      levels: levels.map((level) => {
        const entry = asRecord(level)
        return { id: generateClientId(), name: readString(entry.name), definition: readString(entry.definition) }
      }),
    }
  })
}

/** Reads a stored ASSERT request, filling anything missing with defaults. */
export function assertDraftFromRequest(request: Record<string, unknown>): {
  readonly behavior: AssertBehaviorDraft
  readonly settings: AssertSettingsDraft
} {
  const defaults = defaultAssertSettings()
  const sampling = asRecord(request.sampling)
  const dimensions = readDimensions(request.dimensions)
  const stratifyBy = readStringList(sampling.stratify_by)
  const systemPrompt = request.target_system_prompt
  return {
    behavior: {
      behaviorName: readString(request.behavior_name),
      behaviorDescription: readString(request.behavior_description),
    },
    settings: {
      context: readString(request.context),
      groupCount: readNumberText(request.group_count, DEFAULT_GROUP_COUNT),
      includeUserPrompt: readBoolean(request.include_user_prompt, defaults.includeUserPrompt),
      includeAdversarialPrompt: readBoolean(request.include_adversarial_prompt, false),
      behaviorCategoryCount: readNumberText(request.behavior_category_count, DEFAULT_CATEGORY_COUNT),
      dimensionMode: dimensions.some((dimension) => dimension.levels.length > 0) ? 'explicit' : 'describe',
      dimensions,
      dimensionLevelCount: readNumberText(request.dimension_level_count, DEFAULT_LEVEL_COUNT),
      samplingMethod: readSamplingMethod(sampling.method),
      stratifyBy: stratifyBy.length > 0 ? stratifyBy : defaults.stratifyBy,
      replication: sampling.replication === 'none' ? 'none' : 'balanced',
      withReplacement: readBoolean(sampling.with_replacement, defaults.withReplacement),
      randomSeed: readNumberText(request.random_seed, 0),
      webSearch: readBoolean(request.web_search, defaults.webSearch),
      instructionMode: request.target_owns_instructions === true
        ? 'target'
        : typeof systemPrompt === 'string' ? 'custom' : 'default',
      targetSystemPrompt: typeof systemPrompt === 'string' ? systemPrompt : '',
    },
  }
}

/** Dimension names the sampling plan can stratify by. */
export function stratifyOptions(settings: AssertSettingsDraft): string[] {
  const names = settings.dimensions.map((dimension) => dimension.name.trim()).filter(Boolean)
  return [...new Set([RESERVED_DIMENSION_NAME, ...names])]
}

function dimensionErrors(settings: AssertSettingsDraft): string[] {
  const errors: string[] = []
  const seen = new Set<string>()
  for (const [index, dimension] of settings.dimensions.entries()) {
    const name = dimension.name.trim()
    const label = name ? `Dimension "${name}"` : `Dimension ${index + 1}`
    if (!name) errors.push(`${label} needs a name.`)
    else if (name === RESERVED_DIMENSION_NAME) errors.push(`"${RESERVED_DIMENSION_NAME}" is reserved for the taxonomy; rename the dimension.`)
    else if (seen.has(name)) errors.push(`Dimension names must be unique; "${name}" is repeated.`)
    seen.add(name)
    if (settings.dimensionMode === 'describe') {
      if (!dimension.description.trim()) errors.push(`${label} needs a description.`)
      continue
    }
    if (dimension.levels.length < MIN_LEVEL_COUNT) errors.push(`${label} needs at least ${MIN_LEVEL_COUNT} levels.`)
    const levelNames = new Set<string>()
    for (const level of dimension.levels) {
      const levelName = level.name.trim()
      if (!levelName || !level.definition.trim()) {
        errors.push(`Every level of ${label.toLowerCase()} needs a name and a definition.`)
        break
      }
      if (levelNames.has(levelName)) {
        errors.push(`${label} repeats the level "${levelName}".`)
        break
      }
      levelNames.add(levelName)
    }
  }
  return errors
}

/** Problems in the shared settings that would make the server reject the request. */
export function assertSettingsErrors(settings: AssertSettingsDraft): string[] {
  const errors: string[] = []
  if (parseWholeNumber(settings.groupCount, 1, MAX_GROUP_COUNT) === null) {
    errors.push(`Seed groups must be a whole number from 1 to ${MAX_GROUP_COUNT.toLocaleString('en-US')}.`)
  }
  if (!settings.includeUserPrompt && !settings.includeAdversarialPrompt) {
    errors.push('Include a user prompt, an adversarial prompt, or both.')
  }
  if (parseWholeNumber(settings.behaviorCategoryCount, 1, MAX_CATEGORY_COUNT) === null) {
    errors.push(`Suggested categories must be a whole number from 1 to ${MAX_CATEGORY_COUNT.toLocaleString('en-US')}.`)
  }
  if (parseWholeNumber(settings.dimensionLevelCount, MIN_LEVEL_COUNT, MAX_LEVEL_COUNT) === null) {
    errors.push(`Levels per dimension must be a whole number from ${MIN_LEVEL_COUNT} to ${MAX_LEVEL_COUNT}.`)
  }
  errors.push(...dimensionErrors(settings))
  if (settings.samplingMethod === 'stratified') {
    const options = stratifyOptions(settings)
    if (!settings.stratifyBy.some((name) => options.includes(name))) {
      errors.push('Choose at least one dimension to stratify by.')
    }
  }
  if (parseWholeNumber(settings.randomSeed, Number.MIN_SAFE_INTEGER, Number.MAX_SAFE_INTEGER) === null) {
    errors.push('Random seed must be a whole number.')
  }
  if (settings.instructionMode === 'custom' && !settings.targetSystemPrompt.trim()) {
    errors.push('Enter the target system prompt, or choose another instruction option.')
  }
  return errors
}

export function assertBehaviorErrors(behavior: AssertBehaviorDraft): string[] {
  const errors: string[] = []
  if (!behavior.behaviorName.trim()) errors.push('Enter a behavior name.')
  if (!behavior.behaviorDescription.trim()) errors.push('Enter the policy text.')
  return errors
}

/** Builds the request for valid settings; check `assertSettingsErrors` first. */
export function buildAssertRequest(
  settings: AssertSettingsDraft,
  behavior: AssertBehaviorDraft,
): AssertGenerationRequest {
  const method = settings.samplingMethod
  const options = stratifyOptions(settings)
  const dimensions: AssertDimension[] = settings.dimensions.map((dimension) => ({
    name: dimension.name.trim(),
    description: dimension.description.trim(),
    levels: settings.dimensionMode === 'explicit'
      ? dimension.levels.map((level) => ({ name: level.name.trim(), definition: level.definition.trim() }))
      : [],
  }))
  return {
    behavior_name: behavior.behaviorName.trim(),
    behavior_description: behavior.behaviorDescription,
    context: settings.context,
    group_count: Number(settings.groupCount),
    include_user_prompt: settings.includeUserPrompt,
    include_adversarial_prompt: settings.includeAdversarialPrompt,
    behavior_category_count: Number(settings.behaviorCategoryCount),
    dimensions,
    dimension_level_count: Number(settings.dimensionLevelCount),
    sampling: {
      method,
      stratify_by: method === 'stratified'
        ? settings.stratifyBy.filter((name) => options.includes(name))
        : [RESERVED_DIMENSION_NAME],
      replication: method === 'full_factorial' ? settings.replication : 'balanced',
      with_replacement: method === 'random' ? settings.withReplacement : true,
    },
    random_seed: Number(settings.randomSeed),
    web_search: settings.webSearch,
    target_system_prompt: settings.instructionMode === 'custom' ? settings.targetSystemPrompt : null,
    target_owns_instructions: settings.instructionMode === 'target',
  }
}

/** The default dataset name for one behavior of a suite: `<suite>_<behavior>`. */
export function suiteDatasetName(prefix: string, behaviorName: string): string {
  const cleanPrefix = datasetNameFromText(prefix)
  return datasetNameFromText(cleanPrefix ? `${cleanPrefix}_${behaviorName}` : behaviorName)
}
