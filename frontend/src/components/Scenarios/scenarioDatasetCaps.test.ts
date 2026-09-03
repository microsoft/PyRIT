import type { ScenarioRunEstimateDataset, ScenarioRunEstimateDatasetCap } from '@/types'

import { normalizeDatasetCaps } from './scenarioDatasetCaps'

const SHARED_CAP: ScenarioRunEstimateDatasetCap = {
  id: 'shared-cap',
  label: 'combined cap',
  count: 10,
  configuredOn: 'compound',
  datasetName: null,
}

function makeDataset(
  name: string,
  configuredCaps: ScenarioRunEstimateDatasetCap[],
): ScenarioRunEstimateDataset {
  return {
    id: name,
    name,
    kind: 'dataset',
    logicalSeedGroupCount: 20,
    selectedSeedGroupCount: 5,
    configuredCaps,
    selectionNote: null,
  }
}

describe('scenarioDatasetCaps', () => {
  it('returns empty normalized collections without datasets', () => {
    const normalized = normalizeDatasetCaps([])

    expect(normalized.commonCaps).toEqual([])
    expect(normalized.residualCapsByDatasetId.size).toBe(0)
  })

  it('lifts compound caps and preserves single-dataset row caps', () => {
    const rowCap: ScenarioRunEstimateDatasetCap = {
      ...SHARED_CAP,
      id: 'row-cap',
      configuredOn: 'dataset',
      datasetName: 'alpha',
    }
    const dataset = makeDataset('alpha', [SHARED_CAP, rowCap])
    const normalized = normalizeDatasetCaps([dataset])

    expect(normalized.commonCaps).toEqual([SHARED_CAP])
    expect(normalized.residualCapsByDatasetId.get('alpha')).toEqual([rowCap])
  })

  it('preserves cap multiplicity while separating universal and residual row caps', () => {
    const rowCap: ScenarioRunEstimateDatasetCap = {
      id: 'row-cap',
      label: 'per-dataset cap',
      count: 5,
      configuredOn: 'dataset',
      datasetName: null,
    }
    const datasets = [
      makeDataset('alpha', [SHARED_CAP, rowCap, { ...rowCap, id: 'row-cap-duplicate' }]),
      makeDataset('beta', [{ ...SHARED_CAP, id: 'shared-cap-beta' }, rowCap]),
    ]
    const normalized = normalizeDatasetCaps(datasets)

    expect(normalized.commonCaps).toEqual([SHARED_CAP, rowCap])
    expect(normalized.residualCapsByDatasetId.get('alpha')).toEqual([
      { ...rowCap, id: 'row-cap-duplicate' },
    ])
    expect(normalized.residualCapsByDatasetId.get('beta')).toEqual([])
  })
})
