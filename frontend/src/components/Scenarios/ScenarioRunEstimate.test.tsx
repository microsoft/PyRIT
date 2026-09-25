import type { ReactNode } from 'react'

import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ScenarioRunEstimateState, ScenarioRunSizeEstimateResponse } from '@/types'

import {
  ScenarioRunEstimateDetails,
  ScenarioRunEstimateSummary,
} from './ScenarioRunEstimate'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const EXACT_ESTIMATE: ScenarioRunSizeEstimateResponse = {
  estimated_attack_count: 8,
  components: [
    {
      label: 'Prompt sending',
      count: 6,
      is_baseline: false,
      note: 'One planned attack per selected objective and template.',
    },
    {
      label: 'Baseline attack',
      count: 2,
      is_baseline: true,
      note: null,
    },
  ],
  datasets: [
    {
      name: 'harmbench',
      kind: 'dataset',
      logical_seed_group_count: 4,
      selected_seed_group_count: 4,
      configured_caps: [
        {
          label: 'Jailbreak templates',
          count: 2,
          configured_on: 'configuration',
          dataset_name: null,
        },
      ],
      selection_note: 'Four compatible objective groups selected.',
    },
  ],
  note: 'The backend total is authoritative.',
}

describe('ScenarioRunEstimate', () => {
  it('labels a configuration-based total as approximate without population counts', () => {
    const state = mapScenarioRunEstimate({
      ...EXACT_ESTIMATE,
      status: 'approximate',
      configured_dataset_size: 5,
      datasets: [{
        name: 'not-loaded',
        kind: 'dataset',
        logical_seed_group_count: null,
        selected_seed_group_count: null,
        configured_caps: [],
        selection_note: null,
      }],
    }, 'default')
    render(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={state} />
      </TestWrapper>,
    )
    expect(screen.getByText('About 8 attacks')).toBeInTheDocument()
    expect(screen.getByText('Approximate estimate')).toBeInTheDocument()
    expect(screen.queryByText('Estimate unavailable')).not.toBeInTheDocument()
  })

  it('renders only the authoritative total and formula in the detailed preview', () => {
    const state = mapScenarioRunEstimate(EXACT_ESTIMATE, 'request')

    render(
      <TestWrapper>
        <ScenarioRunEstimateDetails state={state} />
      </TestWrapper>,
    )

    expect(screen.getByText('8 attacks')).toBeInTheDocument()
    expect(screen.getByText(
      'Prompt sending: 6 + Baseline attack: 2 = 8',
    )).toBeInTheDocument()
    expect(screen.queryByText('Backend estimate')).not.toBeInTheDocument()
    expect(screen.queryByText('Current configuration')).not.toBeInTheDocument()
    expect(screen.queryByText('Planned components')).not.toBeInTheDocument()
    expect(screen.queryByText('Dataset populations')).not.toBeInTheDocument()
  })

  it.each<[number | null, number | null, string]>([
    [12, 20, 'About 12-20 attacks'],
    [12, 12, 'About 12 attacks'],
    [null, 20, 'Up to about 20 attacks'],
    [12, null, 'At least about 12 attacks'],
  ])('labels budget ranges as approximate (%s, %s)', (minimum, maximum, label) => {
    const state = mapScenarioRunEstimate({
      ...EXACT_ESTIMATE,
      status: 'conditional',
      configured_dataset_size: 4,
      estimated_attack_count: null,
      minimum_attack_count: minimum,
      maximum_attack_count: maximum,
      components: [],
    }, 'default')
    render(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={state} />
      </TestWrapper>,
    )
    expect(screen.getByText(label)).toBeInTheDocument()
  })

  it('supports loading, conditional null totals, unavailable, and stale states', () => {
    const loading: ScenarioRunEstimateState = { status: 'loading', scope: 'request' }
    const { rerender } = render(
      <TestWrapper>
        <ScenarioRunEstimateDetails state={loading} />
      </TestWrapper>,
    )
    expect(screen.getByText('Calculating run estimate...')).toBeInTheDocument()

    const conditional = mapScenarioRunEstimate({
      ...EXACT_ESTIMATE,
      estimated_attack_count: null,
      minimum_attack_count: 12,
      maximum_attack_count: 20,
      components: [{ label: 'Possible attacks', count: 20, is_baseline: false, note: null }],
      datasets: [],
      note: null,
    }, 'default')
    rerender(
      <TestWrapper>
        <ScenarioRunEstimateDetails state={conditional} />
      </TestWrapper>,
    )
    expect(screen.getByText('12-20 attacks')).toBeInTheDocument()
    expect(screen.getByText(
      'Possible attacks: 20 = conditional total',
    )).toBeInTheDocument()

    const unavailable = mapScenarioRunEstimate({
      ...EXACT_ESTIMATE,
      estimated_attack_count: null,
      minimum_attack_count: null,
      maximum_attack_count: null,
      components: [],
      datasets: [],
      note: 'Target capability is not available.',
    }, 'request')
    rerender(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={unavailable} />
        <ScenarioRunEstimateDetails state={unavailable} />
      </TestWrapper>,
    )
    expect(screen.getByText('Estimate unavailable')).toBeInTheDocument()
    expect(screen.getByText('Configured run size unavailable')).toBeInTheDocument()
    expect(screen.getByText('Target capability is not available.')).toBeInTheDocument()

    const exact = mapScenarioRunEstimate(EXACT_ESTIMATE, 'request')
    if (exact.status !== 'available') {
      throw new Error('Expected exact estimate to map to an available state.')
    }
    const stale: ScenarioRunEstimateState = {
      status: 'stale',
      estimate: exact.estimate,
      label: 'Showing the last successful estimate.',
      error: 'Preview service timed out.',
    }
    rerender(
      <TestWrapper>
        <ScenarioRunEstimateDetails state={stale} />
      </TestWrapper>,
    )
    expect(screen.getByText('8 attacks')).toBeInTheDocument()
    expect(screen.queryByText('Showing the last successful estimate.')).not.toBeInTheDocument()
    expect(screen.queryByText('Preview service timed out.')).not.toBeInTheDocument()
  })
})
