import type { ScenarioRunSizeEstimateResponse } from '@/types'

import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'

const UNKNOWN: ScenarioRunSizeEstimateResponse = {
  status: 'unavailable',
  dataset_size: { kind: 'indeterminate', detail: 'The provider could not report its population.' },
  dataset_limit: { state: 'scenario_default' },
  estimated_attack_count: null,
  components: [],
  datasets: [],
  note: 'The provider could not report its population.',
}

describe('scenario run estimate presentation', () => {
  it('presents All available as a normal deferred count without inventing a total', () => {
    expect(mapScenarioRunEstimate({ ...UNKNOWN, dataset_size: { kind: 'all_available' } }, 'request')).toEqual({
      status: 'unavailable',
      scope: 'request',
      label: 'Exact count is calculated when the scan starts.',
      deferredCount: true,
    })
  })

  it('retains a genuinely indeterminate estimate and its reason', () => {
    expect(mapScenarioRunEstimate(UNKNOWN, 'request')).toEqual({
      status: 'unavailable', scope: 'request', label: 'Configured run size unavailable', note: UNKNOWN.note,
    })
  })
})
