import { useCallback, useState } from 'react'

import { scenarioPresetsApi, scenarioSuitesApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioPresetResponse, ScenarioSuite, ScenarioSuiteResponse } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { suggestRegistryName } from '@/utils/registryName'

const SUITE_SCHEMA_VERSION = 1

/**
 * How a change ended. `partial` means the scenario was saved but is not in the preset
 * yet; retry with `savedPresetName` so the same scenario is added instead of a copy.
 */
export type SuiteMutationOutcome =
  | { readonly status: 'done' }
  | { readonly status: 'failed'; readonly error: string }
  | { readonly status: 'partial'; readonly error: string; readonly savedPresetName: string }

export interface NewSuiteInput {
  readonly name: string
  readonly description: string
  readonly scenarioName: string
}

export interface ScenarioSuiteMutations {
  readonly busy: boolean
  /** Saves the first scenario with its scenario's defaults, then the preset that holds it. */
  readonly createSuite: (input: NewSuiteInput, savedPresetName?: string) => Promise<SuiteMutationOutcome>
  /** Saves a scenario with its scenario's defaults, then appends it to the preset. */
  readonly addScenario: (
    item: ScenarioSuiteResponse,
    scenarioName: string,
    savedPresetName?: string,
  ) => Promise<SuiteMutationOutcome>
  /** Takes a scenario out of the preset. The stored scenario itself is kept. */
  readonly removeEntry: (item: ScenarioSuiteResponse, entryId: string) => Promise<SuiteMutationOutcome>
  readonly updateDescription: (item: ScenarioSuiteResponse, description: string) => Promise<SuiteMutationOutcome>
  /** Deletes the preset. The scenarios it referenced stay stored. */
  readonly deleteSuite: (item: ScenarioSuiteResponse) => Promise<SuiteMutationOutcome>
}

/** A 409 on a write that sent an expected version means someone else changed the preset first. */
function errorMessage(err: unknown, name: string): string {
  const apiError = toApiError(err)
  return apiError.status === 409
    ? `'${name}' changed since it was loaded. Review it and try again.`
    : apiError.detail
}

function failure(err: unknown, name: string): SuiteMutationOutcome {
  return { status: 'failed', error: errorMessage(err, name) }
}

function withDescription(suite: ScenarioSuite, description: string): ScenarioSuite {
  const next: ScenarioSuite = { ...suite }
  const trimmed = description.trim()
  if (trimmed === '') {
    delete next.description
  } else {
    next.description = trimmed
  }
  return next
}

function appendEntry(suite: ScenarioSuite, presetName: string): ScenarioSuite {
  return { ...suite, entries: [...suite.entries, { entry_id: generateClientId(), preset_name: presetName }] }
}

/**
 * Creates and changes presets (backend scenario suites) through the existing suite and
 * preset endpoints. Adding a scenario takes two writes, so a failure between them is
 * reported as `partial` rather than hidden. `onChanged` reloads the library after any
 * write, including a failed one, so the next attempt sees current versions.
 */
export function useScenarioSuiteMutations(
  presets: ReadonlyMap<string, ScenarioPresetResponse>,
  onChanged: () => void,
): ScenarioSuiteMutations {
  const [busy, setBusy] = useState(false)

  const run = useCallback(async (
    write: () => Promise<SuiteMutationOutcome>,
  ): Promise<SuiteMutationOutcome> => {
    setBusy(true)
    try {
      return await write()
    } finally {
      setBusy(false)
      onChanged()
    }
  }, [onChanged])

  const saveScenario = useCallback(async (suiteName: string, scenarioName: string): Promise<string> => {
    const name = suggestRegistryName(suiteName, new Set(presets.keys()), scenarioName)
    const saved = await scenarioPresetsApi.create({ name, scenario_name: scenarioName })
    return saved.preset.name
  }, [presets])

  const createSuite = useCallback((
    input: NewSuiteInput,
    savedPresetName?: string,
  ): Promise<SuiteMutationOutcome> => run(async () => {
    let presetName = savedPresetName
    if (presetName === undefined) {
      try {
        presetName = await saveScenario(input.name, input.scenarioName)
      } catch (err: unknown) {
        return failure(err, input.name)
      }
    }
    const suite = withDescription(
      { name: input.name, schema_version: SUITE_SCHEMA_VERSION, entries: [] },
      input.description,
    )
    try {
      await scenarioSuitesApi.create(appendEntry(suite, presetName))
      return { status: 'done' }
    } catch (err: unknown) {
      return { status: 'partial', error: toApiError(err).detail, savedPresetName: presetName }
    }
  }), [run, saveScenario])

  const addScenario = useCallback((
    item: ScenarioSuiteResponse,
    scenarioName: string,
    savedPresetName?: string,
  ): Promise<SuiteMutationOutcome> => run(async () => {
    const { name } = item.suite
    if (item.version === null) {
      return { status: 'failed', error: `'${name}' is defined in code and cannot be changed here.` }
    }
    let presetName = savedPresetName
    if (presetName === undefined) {
      try {
        presetName = await saveScenario(name, scenarioName)
      } catch (err: unknown) {
        return failure(err, name)
      }
    }
    try {
      // A retry follows a failed write, often a 409, so it appends to the preset as it is now.
      const current = savedPresetName === undefined ? item : await scenarioSuitesApi.get(name)
      if (current.version === null) {
        return { status: 'failed', error: `'${name}' is defined in code and cannot be changed here.` }
      }
      await scenarioSuitesApi.update(name, appendEntry(current.suite, presetName), current.version)
      return { status: 'done' }
    } catch (err: unknown) {
      return { status: 'partial', error: errorMessage(err, name), savedPresetName: presetName }
    }
  }), [run, saveScenario])

  const update = useCallback((
    item: ScenarioSuiteResponse,
    next: ScenarioSuite,
  ): Promise<SuiteMutationOutcome> => run(async () => {
    if (item.version === null) {
      return { status: 'failed', error: `'${item.suite.name}' is defined in code and cannot be changed here.` }
    }
    try {
      await scenarioSuitesApi.update(item.suite.name, next, item.version)
      return { status: 'done' }
    } catch (err: unknown) {
      return failure(err, item.suite.name)
    }
  }), [run])

  const removeEntry = useCallback((item: ScenarioSuiteResponse, entryId: string) => update(item, {
    ...item.suite,
    entries: item.suite.entries.filter((entry) => entry.entry_id !== entryId),
  }), [update])

  const updateDescription = useCallback(
    (item: ScenarioSuiteResponse, description: string) => update(item, withDescription(item.suite, description)),
    [update],
  )

  const deleteSuite = useCallback((item: ScenarioSuiteResponse): Promise<SuiteMutationOutcome> => run(async () => {
    try {
      await scenarioSuitesApi.remove(item.suite.name, item.version ?? undefined)
      return { status: 'done' }
    } catch (err: unknown) {
      return failure(err, item.suite.name)
    }
  }), [run])

  return { busy, createSuite, addScenario, removeEntry, updateDescription, deleteSuite }
}
