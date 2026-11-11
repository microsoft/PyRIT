import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { useAssertPolicyMetrics } from '@/hooks/useAssertPolicyMetrics'
import type { AssertPolicyMetrics, AssertTechniqueMetrics, AssertBinaryRate } from '@/types'

import AssertPolicyMetricsPanel from './AssertPolicyMetricsPanel'
import { isAssertPolicyRun } from './assertPolicyRun'

jest.mock('@/hooks/useAssertPolicyMetrics', () => ({ useAssertPolicyMetrics: jest.fn() }))
const mockUseMetrics = jest.mocked(useAssertPolicyMetrics)

function rate(flagged: number, applicable: number, notApplicable = 0): AssertBinaryRate {
  return {
    rate: applicable ? flagged / applicable : null,
    flagged_count: flagged,
    applicable_count: applicable,
    not_applicable_count: notApplicable,
    ci_lower: applicable ? 0.05 : null,
    ci_upper: applicable ? 0.6 : null,
    sufficient_support: applicable >= 5,
  }
}

function technique(name: string, overrides: Partial<AssertTechniqueMetrics> = {}): AssertTechniqueMetrics {
  return {
    technique: name,
    total: 8,
    scored_total: 8,
    not_judged: { target_input_refused: 0, target_error: 0, judge_no_verdict: 0, attack_error: 0, not_scored: 0 },
    unresolved_policy: 0,
    rows_without_node_judgments: 0,
    policy_violation_on_not_permissible: rate(2, 4, 4),
    policy_violation_on_permissible: rate(1, 6, 2),
    dimensions: { policy_violation: rate(3, 8), overrefusal: rate(1, 8), quality_failure: rate(2, 6, 2) },
    dimension_no_verdict: { policy_violation: 0, overrefusal: 1, quality_failure: 0 },
    dimension_grades: { quality_failure: { counts: { '1': 4, '2': 1, '3': 1 }, median: 1 } },
    categories: [
      { behavior_name: 'support', name: 'public_policy', permissible: true, node_violation: rate(1, 6), attributed_by_fallback: 0 },
      { behavior_name: 'support', name: 'private_disclosure', permissible: false, node_violation: rate(2, 4), attributed_by_fallback: 0 },
    ],
    ...overrides,
  }
}

const METRICS: AssertPolicyMetrics = {
  scenario_result_id: 'run',
  judgment_mode: 'category_validated',
  split_unavailable_reason: null,
  total: 16,
  scored_total: 16,
  techniques: { baseline: technique('baseline'), red_teaming: technique('red_teaming') },
}

function renderPanel(metrics: AssertPolicyMetrics = METRICS): void {
  mockUseMetrics.mockReturnValue({ status: 'success', metrics, error: null, retry: jest.fn() })
  render(<FluentProvider theme={webLightTheme}><AssertPolicyMetricsPanel scenarioResultId="run" /></FluentProvider>)
}

describe('AssertPolicyMetricsPanel', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows the split and each judge dimension per technique with counts and low-support badges', () => {
    renderPanel()

    const table = screen.getByRole('table', { name: 'ASSERT rates' })
    const headers = within(table).getAllByRole('columnheader').map((header) => header.textContent)
    expect(headers).toEqual([
      'Technique', 'Judged', 'Impermissible violated', 'Permissible violated',
      'policy violation', 'overrefusal', 'quality failure',
    ])
    const single = within(table).getByRole('row', { name: /red_teaming/ })
    expect(single).toHaveTextContent('8 of 8')
    expect(single).toHaveTextContent('50.0%2 / 4 · 4 n/a90% CI 5.0%–60.0%')
    expect(single).toHaveTextContent('12.5%1 / 8 · 1 no verdict')
    expect(single).toHaveTextContent('16.7%1 / 6 · 2 n/a')
    expect(single).toHaveTextContent('33.3%2 / 6 · 2 n/a')
    expect(within(single).getByText('median 1 · 1: 4, 2: 1, 3: 1')).toBeInTheDocument()
    expect(within(single).getAllByText('n < 5')).toHaveLength(1)
    expect(within(table).getByRole('row', { name: /Baseline/ })).toBeInTheDocument()
    expect(mockUseMetrics).toHaveBeenCalledWith('run')
  })

  it('warns about conversations left out of the rates', () => {
    renderPanel({
      ...METRICS,
      techniques: {
        red_teaming: technique('red_teaming', {
          scored_total: 7,
          not_judged: { target_input_refused: 1, target_error: 0, judge_no_verdict: 0, attack_error: 0, not_scored: 0 },
          unresolved_policy: 2,
          dimension_no_verdict: { policy_violation: 1, overrefusal: 0, quality_failure: 0 },
        }),
      },
    })

    expect(screen.getByText(
      'Some conversations are left out of these rates. red_teaming not judged: 1 target blocked; '
        + 'left out of the stance rates only: 2 without a stored policy, 1 without a policy verdict.',
    )).toBeInTheDocument()
  })

  it('shows per-category rates on demand', async () => {
    const user = userEvent.setup()
    renderPanel()

    await user.click(screen.getByRole('button', { name: 'Rates by policy category' }))
    const categories = screen.getByRole('table', { name: 'Rates by policy category' })
    expect(within(categories).getByRole('row', { name: /private_disclosure/ })).toHaveTextContent('Impermissible50.0%')
  })

  it('explains when the split needs category judgments', () => {
    renderPanel({
      ...METRICS,
      judgment_mode: 'direct',
      split_unavailable_reason: 'node_judgments_unavailable',
      techniques: {
        red_teaming: technique('red_teaming', {
          policy_violation_on_not_permissible: null,
          policy_violation_on_permissible: null,
          rows_without_node_judgments: 8,
          categories: [],
        }),
      },
    })

    expect(screen.getByText(/category_validated judgment mode/)).toBeInTheDocument()
    expect(screen.queryByText(/left out of these rates/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Rates by policy category' })).not.toBeInTheDocument()
  })

  it('renders nothing when the backend identifies another scenario', () => {
    mockUseMetrics.mockReturnValue({ status: 'not_assert', metrics: null, error: null, retry: jest.fn() })
    render(<FluentProvider theme={webLightTheme}><AssertPolicyMetricsPanel scenarioResultId="run" /></FluentProvider>)

    expect(screen.queryByTestId('assert-policy-metrics')).not.toBeInTheDocument()
  })

  it('offers a retry after a load error', async () => {
    const user = userEvent.setup()
    const retry = jest.fn()
    mockUseMetrics.mockReturnValue({ status: 'error', metrics: null, error: 'Run not yet completed', retry })
    render(<FluentProvider theme={webLightTheme}><AssertPolicyMetricsPanel scenarioResultId="run" /></FluentProvider>)

    expect(screen.getByText(/Couldn't load ASSERT metrics. Run not yet completed/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(retry).toHaveBeenCalledTimes(1)
  })
})

describe('isAssertPolicyRun', () => {
  it('lets a stored registry name decide', () => {
    expect(isAssertPolicyRun({ scenario_registry_name: 'foundry.assert_policy', scenario_name: 'X' })).toBe(true)
    expect(isAssertPolicyRun({ scenario_registry_name: 'foundry.red_team_agent', scenario_name: 'RedTeamAgent' })).toBe(false)
    expect(isAssertPolicyRun({ scenario_registry_name: 'custom.scan', scenario_name: 'AssertPolicy' })).toBe(false)
  })

  it('matches older runs without a registry name by class name', () => {
    expect(isAssertPolicyRun({ scenario_registry_name: null, scenario_name: 'AssertPolicy' })).toBe(true)
    expect(isAssertPolicyRun({ scenario_name: 'AssertPolicy' })).toBe(true)
    expect(isAssertPolicyRun({ scenario_registry_name: null, scenario_name: 'ContentHarms' })).toBe(false)
  })
})
