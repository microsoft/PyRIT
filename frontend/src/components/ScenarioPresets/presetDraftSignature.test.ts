import type { ScenarioConfigFormState } from '@/components/Scenarios/scenarioConfigForm'
import type { ScenarioDatasetRequest, ScenarioDatasetSourceInput } from '@/types'

import { presetDraftSignature, type PresetDraft } from './presetDraftSignature'

type ConfigPatch = Partial<Omit<ScenarioConfigFormState, 'datasets'>> & { datasets?: ScenarioDatasetRequest }
type DraftPatch = Partial<Omit<PresetDraft, 'config'>> & { config?: ConfigPatch | null }

const CLEAN: ScenarioConfigFormState = {
  techniques: ['crescendo'],
  includeBaseline: false,
  datasets: {},
  scenarioParamValues: {},
}

function makeDraft({ config, ...identity }: DraftPatch = {}): PresetDraft {
  return {
    name: 'nightly_probe',
    description: 'Nightly smoke test.',
    showInScanner: true,
    scenarioName: 'foundry.red_team_agent',
    ...identity,
    config: config === null ? null : { ...CLEAN, ...config },
  }
}

function makeRequest(datasets: ScenarioDatasetRequest): PresetDraft {
  return makeDraft({ config: { datasets } })
}

/** What the API returns for a preset that pinned two datasets: every field is present, each unset one as its default. */
const RETURNED_SOURCES: ScenarioDatasetSourceInput[] = [
  { name: 'harmbench', max_size: 'default', fetch: null },
  { name: 'xstest', max_size: 'default', fetch: null },
]
const RETURNED: ScenarioDatasetRequest = {
  sources: RETURNED_SOURCES,
  max_per_dataset: 'default',
  max_total: 'default',
  fetch: null,
  filters: null,
}

describe('presetDraftSignature', () => {
  describe('reads the same selection as the same draft', () => {
    it.each<[string, ScenarioDatasetRequest, ScenarioDatasetRequest]>([
      ['a source the picker added back', RETURNED,
        { ...RETURNED, sources: [RETURNED_SOURCES[1], { name: 'harmbench' }] }],
      ['a source whose limit was reset', RETURNED,
        { ...RETURNED, sources: [{ name: 'harmbench', max_size: 'default' }, RETURNED_SOURCES[1]] }],
      ['sources in another order', RETURNED,
        { ...RETURNED, sources: [RETURNED_SOURCES[1], RETURNED_SOURCES[0]] }],
      ['a total reset to the default', RETURNED,
        { sources: RETURNED_SOURCES, max_per_dataset: 'default', fetch: null, filters: null }],
      ['a total typed again after a reset', { ...RETURNED, max_total: 20 },
        { sources: RETURNED_SOURCES, max_per_dataset: 'default', fetch: null, filters: null, max_total: 20 }],
      ['a limit that is null, the default, or left out', { max_per_dataset: null }, { max_per_dataset: 'default' }],
      ['a loading policy that is the default or null', { fetch: 'default' }, { fetch: null }],
      ['a dataset policy that is the default or null',
        { sources: [{ name: 'harmbench', fetch: 'default' }] }, { sources: [{ name: 'harmbench', fetch: null }] }],
      ['no sources, as an empty list or left out',
        { sources: [], max_total: 20 }, { max_total: 20 }],
      ['no filters, as null or left out', { sources: RETURNED_SOURCES, filters: null }, { sources: RETURNED_SOURCES }],
      ['no filters, as an empty object or left out', { sources: RETURNED_SOURCES, filters: {} }, { sources: RETURNED_SOURCES }],
      ['filter keys and values in another order',
        { filters: { data_types: ['text'], harm_categories: ['violence', 'hate'] } },
        { filters: { harm_categories: ['hate', 'violence'], data_types: ['text'] } }],
      ['request keys in another order',
        { max_total: 20, filters: { harm_categories: ['hate'] }, sources: RETURNED_SOURCES },
        { sources: RETURNED_SOURCES, max_total: 20, filters: { harm_categories: ['hate'] } }],
    ])('%s', (_label, left, right) => {
      expect(presetDraftSignature(makeRequest(left))).toBe(presetDraftSignature(makeRequest(right)))
    })

    it.each<[string, DraftPatch, DraftPatch]>([
      ['techniques in another order', { config: { techniques: ['judge', 'crescendo'] } },
        { config: { techniques: ['crescendo', 'judge'] } }],
      ['parameter fields in another order',
        { config: { scenarioParamValues: { retries: '2', attempts: '3' } } },
        { config: { scenarioParamValues: { attempts: '3', retries: '2' } } }],
    ])('%s', (_label, left, right) => {
      expect(presetDraftSignature(makeDraft(left))).toBe(presetDraftSignature(makeDraft(right)))
    })
  })

  describe('reads a different selection as an edit', () => {
    it.each<[string, ScenarioDatasetRequest, ScenarioDatasetRequest]>([
      ['a pinned dataset', { sources: [{ name: 'harmbench' }] }, {}],
      ['another dataset', { sources: [{ name: 'xstest' }] }, { sources: [{ name: 'harmbench' }] }],
      ['a dataset limit', { sources: [{ name: 'harmbench', max_size: 5 }] }, { sources: [{ name: 'harmbench' }] }],
      ['a total', { max_total: 20 }, {}],
      ['all available against the default', { max_total: 'all' }, {}],
      ['a per dataset limit', { max_per_dataset: 5 }, {}],
      ['a pinned loading policy', { fetch: 'always' }, {}],
      ['a loading policy on one dataset',
        { sources: [{ name: 'harmbench', fetch: 'never' }] }, { sources: [{ name: 'harmbench' }] }],
      ['a filter', { filters: { harm_categories: ['hate'] } }, {}],
      ['a filter value removed',
        { filters: { harm_categories: ['hate'] } }, { filters: { harm_categories: ['hate', 'violence'] } }],
      ['an empty filter list, which overrides a default subset', { filters: { harm_categories: [] } }, {}],
    ])('%s', (_label, left, right) => {
      expect(presetDraftSignature(makeRequest(left))).not.toBe(presetDraftSignature(makeRequest(right)))
    })

    it.each<[string, DraftPatch, DraftPatch]>([
      ['a technique', { config: { techniques: ['crescendo', 'judge'] } }, { config: { techniques: ['crescendo'] } }],
      ['an ordered parameter list',
        { config: { scenarioParamValues: { tags: ['b', 'a'] } } }, { config: { scenarioParamValues: { tags: ['a', 'b'] } } }],
      ['a parameter', { config: { scenarioParamValues: { attempts: '3' } } },
        { config: { scenarioParamValues: { attempts: '5' } } }],
      ['the baseline', { config: { includeBaseline: true } }, { config: { includeBaseline: false } }],
      ['no configuration against one', { config: null }, {}],
      ['the name', { name: 'other' }, {}],
      ['the description', { description: 'Other.' }, {}],
      ['the scanner switch', { showInScanner: false }, {}],
      ['the scenario', { scenarioName: 'other' }, {}],
    ])('%s', (_label, left, right) => {
      expect(presetDraftSignature(makeDraft(left))).not.toBe(presetDraftSignature(makeDraft(right)))
    })
  })
})
