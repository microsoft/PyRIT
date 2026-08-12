import type { ReactNode } from 'react'

import { render, screen, within } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ScenarioDefaultRunSizeEstimate, ScenarioRunEstimateState } from '@/types'

import {
  ScenarioRunEstimateDetails,
  ScenarioRunEstimateSummary,
} from './ScenarioRunEstimate'
import { mapScenarioRunEstimate } from './scenarioRunEstimateAdapter'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function makeEstimate(
  overrides: Partial<ScenarioDefaultRunSizeEstimate> = {},
): ScenarioDefaultRunSizeEstimate {
  return {
    version: 1,
    status: 'exact',
    total_attack_count: 16,
    minimum_attack_count: null,
    maximum_attack_count: null,
    condition: null,
    components: [
      {
        label: 'Default technique sweep',
        count: 16,
        factors: [
          { label: 'selected logical seed groups', count: 4 },
          { label: 'default concrete techniques', count: 4 },
        ],
        is_baseline: false,
        note: null,
      },
    ],
    datasets: [
      {
        name: 'harmbench',
        kind: 'dataset',
        logical_seed_group_count: 400,
        selected_seed_group_count: 4,
        configured_caps: [],
        selection_note: 'The default selection uses 4 of 400 available objectives.',
      },
    ],
    adaptive_details: null,
    note: null,
    retries_included: false,
    ...overrides,
  }
}

function renderDetails(estimate: ScenarioDefaultRunSizeEstimate): void {
  render(
    <TestWrapper>
      <ScenarioRunEstimateDetails state={mapScenarioRunEstimate(estimate, 'request')} />
    </TestWrapper>,
  )
}

describe('ScenarioRunEstimate', () => {
  it('renders an exact technique-by-objective equation with a complete accessible sentence', () => {
    renderDetails(makeEstimate())

    const equation = screen.getByRole('group', {
      name: '4 techniques multiplied by 4 objectives equals 16 planned attacks.',
    })
    expect(within(equation).getAllByText('4')).toHaveLength(2)
    expect(within(equation).getByText('techniques')).toBeInTheDocument()
    expect(within(equation).getByText('objectives')).toBeInTheDocument()
    expect(within(equation).getByText('16')).toBeInTheDocument()
    expect(within(equation).getByText('planned attacks')).toBeInTheDocument()
    expect(screen.getByText('4 objectives from harmbench · 400 available')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Run calculation' })).toBeInTheDocument()
  })

  it.each([1, 4, 5])(
    'renders a universal per-dataset cap of %i once before the objective-source rows',
    (capCount) => {
      renderDetails(makeEstimate({
        datasets: [
          {
            name: 'airt_hate',
            kind: 'dataset',
            logical_seed_group_count: 4,
            selected_seed_group_count: 4,
            configured_caps: [{
              label: 'per-dataset cap',
              count: capCount,
              configured_on: 'dataset',
              dataset_name: 'airt_hate',
            }],
            selection_note: null,
          },
          {
            name: 'airt_leakage',
            kind: 'dataset',
            logical_seed_group_count: 9,
            selected_seed_group_count: 5,
            configured_caps: [{
              label: 'per-dataset cap',
              count: capCount,
              configured_on: 'dataset',
              dataset_name: 'airt_leakage',
            }],
            selection_note: null,
          },
        ],
      }))

      const capText = `Per-dataset cap: ${capCount} ${capCount === 1 ? 'objective' : 'objectives'}`
      expect(screen.getAllByText(capText)).toHaveLength(1)
      expect(screen.getByText('4 objectives from airt_hate')).toBeInTheDocument()
      expect(screen.getByText('5 objectives from airt_leakage · 9 available')).toBeInTheDocument()
      const sources = screen.getByRole('group', { name: 'Objective sources' })
      const sourceText = sources.textContent ?? ''
      expect(sourceText.indexOf(capText)).toBeLessThan(sourceText.indexOf('4 objectives from airt_hate'))
    },
  )

  it('keeps differing dataset caps with their affected objective-source rows', () => {
    renderDetails(makeEstimate({
      datasets: [
        {
          name: 'dataset_alpha',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 4,
          configured_caps: [{
            label: 'per-dataset cap',
            count: 4,
            configured_on: 'dataset',
            dataset_name: 'dataset_alpha',
          }],
          selection_note: null,
        },
        {
          name: 'dataset_beta',
          kind: 'dataset',
          logical_seed_group_count: 10,
          selected_seed_group_count: 5,
          configured_caps: [{
            label: 'per-dataset cap',
            count: 5,
            configured_on: 'dataset',
            dataset_name: 'dataset_beta',
          }],
          selection_note: null,
        },
      ],
    }))

    const alpha = screen.getByRole('group', { name: 'Objective source: dataset_alpha' })
    const beta = screen.getByRole('group', { name: 'Objective source: dataset_beta' })
    expect(within(alpha).getByText('Per-dataset cap: 4 objectives')).toBeInTheDocument()
    expect(within(beta).getByText('Per-dataset cap: 5 objectives')).toBeInTheDocument()
    expect(screen.getAllByText(/Per-dataset cap:/)).toHaveLength(2)
  })

  it('keeps a single-dataset cap attached to its objective-source row', () => {
    renderDetails(makeEstimate({
      datasets: [{
        name: 'harmbench',
        kind: 'dataset',
        logical_seed_group_count: 8,
        selected_seed_group_count: 4,
        configured_caps: [{
          label: 'per-dataset cap',
          count: 4,
          configured_on: 'dataset',
          dataset_name: 'harmbench',
        }],
        selection_note: null,
      }],
    }))

    const source = screen.getByRole('group', { name: 'Objective source: harmbench' })
    expect(within(source).getByText('Per-dataset cap: 4 objectives')).toBeInTheDocument()
  })

  it('renders a global cap once while preserving differing per-dataset caps on rows', () => {
    renderDetails(makeEstimate({
      datasets: [
        {
          name: 'dataset_alpha',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 3,
          configured_caps: [
            {
              label: 'per-dataset cap',
              count: 3,
              configured_on: 'dataset',
              dataset_name: 'dataset_alpha',
            },
            {
              label: 'combined compound cap',
              count: 10,
              configured_on: 'compound',
              dataset_name: null,
            },
          ],
          selection_note: null,
        },
        {
          name: 'dataset_beta',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 4,
          configured_caps: [
            {
              label: 'per-dataset cap',
              count: 4,
              configured_on: 'dataset',
              dataset_name: 'dataset_beta',
            },
            {
              label: 'combined compound cap',
              count: 10,
              configured_on: 'compound',
              dataset_name: null,
            },
          ],
          selection_note: null,
        },
      ],
    }))

    expect(screen.getAllByText('Combined compound cap: 10')).toHaveLength(1)
    expect(within(screen.getByRole('group', {
      name: 'Objective source: dataset_alpha',
    })).getByText('Per-dataset cap: 3 objectives')).toBeInTheDocument()
    expect(within(screen.getByRole('group', {
      name: 'Objective source: dataset_beta',
    })).getByText('Per-dataset cap: 4 objectives')).toBeInTheDocument()
  })

  it('keeps a configuration cap on only the rows where it applies', () => {
    renderDetails(makeEstimate({
      datasets: [
        {
          name: 'dataset_alpha',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 4,
          configured_caps: [{
            label: 'shared configuration cap',
            count: 6,
            configured_on: 'configuration',
            dataset_name: null,
          }],
          selection_note: null,
        },
        {
          name: 'dataset_beta',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 4,
          configured_caps: [{
            label: 'shared configuration cap',
            count: 6,
            configured_on: 'configuration',
            dataset_name: null,
          }],
          selection_note: null,
        },
        {
          name: 'dataset_gamma',
          kind: 'dataset',
          logical_seed_group_count: 8,
          selected_seed_group_count: 8,
          configured_caps: [],
          selection_note: null,
        },
      ],
    }))

    expect(within(screen.getByRole('group', {
      name: 'Objective source: dataset_alpha',
    })).getByText('Shared configuration cap: 6')).toBeInTheDocument()
    expect(within(screen.getByRole('group', {
      name: 'Objective source: dataset_beta',
    })).getByText('Shared configuration cap: 6')).toBeInTheDocument()
    expect(within(screen.getByRole('group', {
      name: 'Objective source: dataset_gamma',
    })).queryByText(/Shared configuration cap/)).not.toBeInTheDocument()
    expect(screen.getAllByText('Shared configuration cap: 6')).toHaveLength(2)
  })

  it('renders no cap summary for uncapped datasets and preserves the selection note', () => {
    renderDetails(makeEstimate({
      datasets: [{
        name: 'harmbench',
        kind: 'dataset',
        logical_seed_group_count: 8,
        selected_seed_group_count: 4,
        configured_caps: [],
        selection_note: 'Four compatible objectives remain after filtering.',
      }],
    }))

    expect(screen.queryByText(/cap:/i)).not.toBeInTheDocument()
    expect(screen.getByText('4 objectives from harmbench · 8 available')).toBeInTheDocument()
    expect(screen.getByText('Four compatible objectives remain after filtering.')).toBeInTheDocument()
  })

  it('renders heterogeneous compatibility as truthful per-technique additive terms', () => {
    renderDetails(makeEstimate({
      total_attack_count: 6,
      components: [
        {
          label: 'technique_alpha',
          count: 4,
          factors: [
            { label: 'selected concrete techniques', count: 1 },
            { label: 'compatible logical seed groups', count: 4 },
          ],
          is_baseline: false,
          note: null,
        },
        {
          label: 'technique_beta',
          count: 2,
          factors: [
            { label: 'selected concrete techniques', count: 1 },
            { label: 'compatible logical seed groups', count: 2 },
          ],
          is_baseline: false,
          note: null,
        },
      ],
    }))

    const equation = screen.getByTestId('run-calculation')
    expect(within(equation).getByText('objectives · Technique alpha')).toBeInTheDocument()
    expect(within(equation).getByText('objectives · Technique beta')).toBeInTheDocument()
    expect(equation).toHaveTextContent('4objectives · Technique alpha+2objectives · Technique beta=6planned attacks')
  })

  it('uses parentheses to make baseline precedence explicit', () => {
    renderDetails(makeEstimate({
      total_attack_count: 20,
      components: [
        ...makeEstimate().components,
        {
          label: 'Baseline',
          count: 4,
          factors: [{ label: 'selected logical seed groups', count: 4 }],
          is_baseline: true,
          note: null,
        },
      ],
    }))

    const equation = screen.getByTestId('run-calculation')
    expect(equation).toHaveTextContent('(4techniques×4objectives)+4direct baseline attacks=20planned attacks')
    expect(screen.getByRole('group', {
      name: '( 4 techniques multiplied by 4 objectives ) plus 4 direct baseline attacks equals 20 planned attacks.',
    })).toBeInTheDocument()
  })

  it('keeps guaranteed and target-conditional terms in one bounded equation', () => {
    renderDetails(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      minimum_attack_count: 12,
      maximum_attack_count: 20,
      condition: 'target_capabilities',
      components: [
        {
          label: 'Baseline',
          count: 4,
          factors: [{ label: 'objectives', count: 4 }],
          is_baseline: true,
          note: null,
        },
        {
          label: 'Inline jailbreak delivery',
          count: 8,
          factors: [
            { label: 'objectives', count: 4 },
            { label: 'jailbreak templates', count: 2 },
          ],
          is_baseline: false,
          note: null,
        },
        {
          label: 'Native system-prompt jailbreak delivery',
          count: 8,
          factors: [
            { label: 'objectives', count: 4 },
            { label: 'jailbreak templates', count: 2 },
          ],
          is_baseline: false,
          condition: 'target_capabilities',
          note: null,
        },
      ],
    }))

    const equation = screen.getByTestId('run-calculation')
    expect(equation).toHaveTextContent('4objectives · Inline jailbreak delivery')
    expect(equation).toHaveTextContent('4objectives · Native system-prompt jailbreak delivery · if supported')
    expect(equation).toHaveTextContent('4direct baseline attacks')
    expect(equation).toHaveTextContent('12–20planned attacks')
  })

  it('shows bounded attack attempts and separates progress planning', () => {
    const estimate = makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      components: [],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 2,
        candidate_technique_count: 2,
        max_attempts_per_objective: 3,
        techniques_per_objective_upper_bound: 2,
        technique_attempt_count_upper_bound: 42,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    })
    const state = mapScenarioRunEstimate(estimate, 'request')
    render(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={state} />
        <ScenarioRunEstimateDetails state={state} />
      </TestWrapper>,
    )

    expect(screen.getByText('21 objectives · up to 42 attack attempts')).toBeInTheDocument()
    expect(screen.getByRole('group', {
      name: '21 objectives multiplied by up to 2 Adaptive techniques per objective, the smaller of 2 selected candidates and limit 3, equals up to 42 attack attempts. Direct baseline comparison is not included.',
    })).toBeInTheDocument()
    expect(screen.getByText('2 selected candidates · limit 3')).toBeInTheDocument()
    expect(screen.getByRole('group', {
      name: 'Progress units are confirmed at launch. Progress units track resumable evaluation groups, not every persisted attack attempt.',
    })).toBeInTheDocument()
    expect(screen.queryByText('Exact total')).not.toBeInTheDocument()
    expect(screen.getByText(
      'Attack-attempt totals exclude multi-turn target exchanges and retries. Adaptive techniques run sequentially and stop each objective after the first successful technique. Compatibility may reduce how many candidates each objective can try.',
    )).toBeInTheDocument()
  })

  it('includes a supported baseline inside the unified per-objective attempt factor', () => {
    const estimate = makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      minimum_attack_count: 21,
      maximum_attack_count: 42,
      components: [
        {
          label: 'Baseline',
          count: 21,
          factors: [{ label: 'objectives', count: 21 }],
          is_baseline: true,
          note: null,
        },
        {
          label: 'Adaptive objectives',
          count: 21,
          factors: [{ label: 'compatible objectives', count: 21 }],
          is_baseline: false,
          note: null,
        },
      ],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 2,
        candidate_technique_count: 2,
        max_attempts_per_objective: 3,
        techniques_per_objective_upper_bound: 2,
        technique_attempt_count_upper_bound: 42,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    })
    const state = mapScenarioRunEstimate(estimate, 'request')
    render(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={state} />
        <ScenarioRunEstimateDetails state={state} />
      </TestWrapper>,
    )

    expect(screen.getByText('up to 63 attack attempts · 21–42 progress units')).toBeInTheDocument()
    const attemptEquation = screen.getByRole('group', {
      name: '21 objectives multiplied by 1 direct baseline plus up to 2 Adaptive techniques per objective, the smaller of 2 selected candidates and limit 3, equals up to 63 attack attempts. Direct baseline comparison is included.',
    })
    expect(attemptEquation).toHaveTextContent(
      '21objectives×(1direct baseline per objective+up to 2Adaptive techniques per objective2 selected candidates · limit 3)=up to 63attack attempts',
    )
    expect(screen.getByRole('heading', { name: 'Attack attempts' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Progress planning' })).toBeInTheDocument()
    expect(screen.getByTestId('progress-unit-calculation')).toHaveTextContent('21–42progress units')
    expect(screen.queryByText(/Attempt ceiling:/)).not.toBeInTheDocument()
    expect(screen.queryByText(/objective envelope|logical seed groups|selected seed groups/i)).not.toBeInTheDocument()
  })

  it('removes the baseline factor while retaining the Adaptive attempt ceiling', () => {
    renderDetails(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      minimum_attack_count: null,
      maximum_attack_count: 21,
      components: [
        {
          label: 'Adaptive objectives',
          count: 21,
          factors: [{ label: 'compatible objectives', count: 21 }],
          is_baseline: false,
          note: null,
        },
      ],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 14,
        candidate_technique_count: 14,
        max_attempts_per_objective: 14,
        techniques_per_objective_upper_bound: 14,
        technique_attempt_count_upper_bound: 294,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    }))

    const attemptEquation = screen.getByRole('group', {
      name: '21 objectives multiplied by up to 14 Adaptive techniques per objective, the smaller of 14 selected candidates and limit 14, equals up to 294 attack attempts. Direct baseline comparison is not included.',
    })
    expect(attemptEquation).toHaveTextContent(
      '21objectives×up to 14Adaptive techniques per objective14 selected candidates · limit 14=up to 294attack attempts',
    )
    expect(within(attemptEquation).queryByText(/baseline/)).not.toBeInTheDocument()
    expect(screen.getByTestId('progress-unit-calculation')).toHaveTextContent('Up to 21progress units')
  })

  it('renders exact Adaptive progress units without inventing a range', () => {
    renderDetails(makeEstimate({
      status: 'exact',
      total_attack_count: 42,
      minimum_attack_count: null,
      maximum_attack_count: null,
      components: [
        {
          label: 'Baseline',
          count: 21,
          factors: [{ label: 'objectives', count: 21 }],
          is_baseline: true,
          note: null,
        },
        {
          label: 'Adaptive objectives',
          count: 21,
          factors: [{ label: 'objectives', count: 21 }],
          is_baseline: false,
          note: null,
        },
      ],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 14,
        candidate_technique_count: 14,
        max_attempts_per_objective: 14,
        techniques_per_objective_upper_bound: 14,
        technique_attempt_count_upper_bound: 294,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: false,
      },
    }))

    expect(screen.getByRole('group', {
      name: '21 objectives multiplied by 1 direct baseline plus up to 14 Adaptive techniques per objective, the smaller of 14 selected candidates and limit 14, equals up to 315 attack attempts. Direct baseline comparison is included.',
    })).toBeInTheDocument()
    expect(screen.getByTestId('progress-unit-calculation')).toHaveTextContent('42progress units')
    expect(screen.queryByText('21–42')).not.toBeInTheDocument()
  })

  it('preserves a nonzero Adaptive progress range when no baseline is included', () => {
    renderDetails(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      minimum_attack_count: 5,
      maximum_attack_count: 21,
      components: [],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 2,
        candidate_technique_count: 2,
        max_attempts_per_objective: 2,
        techniques_per_objective_upper_bound: 2,
        technique_attempt_count_upper_bound: 42,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    }))

    expect(screen.getByRole('group', {
      name: '5–21 progress units. Progress units track resumable evaluation groups, not every persisted attack attempt.',
    })).toBeInTheDocument()
    expect(screen.getByTestId('run-calculation')).toHaveTextContent('up to 42attack attempts')
  })

  it('uses the configured max when it is lower than the adaptive candidate pool', () => {
    renderDetails(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      components: [],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 14,
        candidate_technique_count: 5,
        max_attempts_per_objective: 3,
        techniques_per_objective_upper_bound: 3,
        technique_attempt_count_upper_bound: 63,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    }))

    expect(screen.getByText('5 compatible candidates from 14 selected · limit 3')).toBeInTheDocument()
    expect(screen.getByText('up to 63')).toBeInTheDocument()
  })

  it('uses the candidate pool when it is lower than the adaptive max', () => {
    renderDetails(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      components: [],
      adaptive_details: {
        objective_count: 21,
        selected_candidate_technique_count: 2,
        candidate_technique_count: 2,
        max_attempts_per_objective: 5,
        techniques_per_objective_upper_bound: 2,
        technique_attempt_count_upper_bound: 42,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    }))

    expect(screen.getByText('Adaptive techniques per objective')).toBeInTheDocument()
    expect(screen.getByText('2 selected candidates · limit 5')).toBeInTheDocument()
    expect(screen.getByText('up to 42')).toBeInTheDocument()
  })

  it('adapts legacy version-one payloads without the selected candidate count', () => {
    const estimate = mapScenarioRunEstimate(makeEstimate({
      status: 'conditional',
      total_attack_count: null,
      components: [],
      adaptive_details: {
        objective_count: 21,
        candidate_technique_count: 2,
        max_attempts_per_objective: 3,
        techniques_per_objective_upper_bound: 2,
        technique_attempt_count_upper_bound: 42,
        stop_on_first_success: true,
        compatibility_may_reduce_attempts: true,
      },
    }), 'request')

    expect(estimate).toMatchObject({
      status: 'conditional',
      estimate: {
        adaptiveDetails: {
          selectedCandidateTechniqueCount: 2,
        },
      },
    })
  })

  it('preserves loading, unavailable, and unknown conditional states', () => {
    const loading: ScenarioRunEstimateState = { status: 'loading', scope: 'request' }
    const { rerender } = render(
      <TestWrapper><ScenarioRunEstimateDetails state={loading} /></TestWrapper>,
    )
    expect(screen.getByText('Calculating planned attacks...')).toBeInTheDocument()

    const unavailable = mapScenarioRunEstimate(makeEstimate({
      status: 'unavailable',
      total_attack_count: null,
      components: [],
      datasets: [],
      note: 'Target capability is not available.',
    }), 'request')
    rerender(<TestWrapper><ScenarioRunEstimateDetails state={unavailable} /></TestWrapper>)
    expect(screen.getByText('Estimate unavailable')).toBeInTheDocument()
    expect(screen.getByText('Configured run size unavailable')).toBeInTheDocument()

    rerender(
      <TestWrapper>
        <ScenarioRunEstimateDetails state={mapScenarioRunEstimate(makeEstimate({
          status: 'conditional',
          total_attack_count: null,
          minimum_attack_count: null,
          maximum_attack_count: null,
          components: [],
          datasets: [],
        }), 'default')}
        />
      </TestWrapper>,
    )
    expect(screen.getByText('Select targets')).toBeInTheDocument()
    expect(screen.getByText('to calculate')).toBeInTheDocument()
    expect(screen.queryByText('Exact total')).not.toBeInTheDocument()
  })

  it('does not render implementation terminology in the shared estimate surfaces', () => {
    const state = mapScenarioRunEstimate(makeEstimate(), 'request')
    render(
      <TestWrapper>
        <ScenarioRunEstimateSummary state={state} />
        <ScenarioRunEstimateDetails state={state} />
      </TestWrapper>,
    )

    expect(screen.queryByText(/logical seed groups/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/selected seed groups/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/planned components/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/objective envelopes/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/how this count is calculated/i)).not.toBeInTheDocument()
  })
})
