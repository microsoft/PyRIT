import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { scenariosApi } from '@/services/api'
import type { PairedOutcomeStatistics, ScenarioRunPlan } from '@/types'
import PairedOutcomes from './PairedOutcomes'

jest.mock('@/services/api', () => ({ scenariosApi: { getPairedOutcomes: jest.fn() } }))
const request = scenariosApi.getPairedOutcomes as jest.Mock
const plan: ScenarioRunPlan = {
  version: 1, seed_groups: [], atomic_groups: [
    { id: 'base', kind: 'baseline', atomic_attack_name: 'baseline', display_group: 'Baseline', technique_eval_hash: 'b', seed_group_ids: [], tags: [] },
    { id: 'attack', kind: 'attack', atomic_attack_name: 'attack', display_group: 'Attack', technique_eval_hash: 'a', seed_group_ids: [], tags: [] },
    { id: 'other', kind: 'attack', atomic_attack_name: 'other', display_group: 'Other attack', technique_eval_hash: 'c', seed_group_ids: [], tags: [] },
  ],
}
const report: PairedOutcomeStatistics = {
  baseline_group_id: 'base', attack_group_id: 'attack', planned_pairs: 6, decided_pairs: 4, excluded_pairs: 2,
  failure_to_success: 1, success_to_failure: 1, failure_to_failure: 1, success_to_success: 1,
  baseline_success_rate: 0.5, attack_success_rate: 0.5, paired_difference: 0,
  missing_baseline: 0, missing_attack: 1, missing_both: 0,
  baseline_errors: 0, attack_errors: 1, baseline_undetermined: 0, attack_undetermined: 0,
  unmatched_baseline_seeds: 1, unmatched_attack_seeds: 0, unattributed_attempts: 0,
}
function mount(props: Partial<React.ComponentProps<typeof PairedOutcomes>> = {}) {
  return render(<FluentProvider theme={webLightTheme}><PairedOutcomes scenarioResultId="run" status="COMPLETED" plan={plan} {...props} /></FluentProvider>)
}
async function compare() {
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText('Baseline group'), 'base')
  await user.selectOptions(screen.getByLabelText('Attack group'), 'attack')
  await user.click(screen.getByRole('button', { name: 'Compare outcomes' }))
  return user
}
beforeEach(() => { request.mockReset(); request.mockResolvedValue(report) })

test('requires explicit groups and shows backend transitions, paired rates and exclusions', async () => {
  mount()
  expect(screen.getByRole('button', { name: 'Compare outcomes' })).toBeDisabled()
  expect(request).not.toHaveBeenCalled()
  await compare()
  const results = await screen.findByRole('region', { name: 'Paired comparison' })
  expect(within(results).getByRole('group', { name: 'Baseline success rate' })).toHaveTextContent('50.0%')
  expect(within(results).getByRole('group', { name: 'Paired difference' })).toHaveTextContent('0.0 pp')
  expect(within(results).getByRole('group', { name: 'Decided pairs' })).toHaveTextContent('4 / 6')
  for (const name of ['Failure → Success', 'Success → Failure', 'Failure → Failure', 'Success → Success']) {
    expect(within(results).getByRole('row', { name: `${name} 1` })).toBeInTheDocument()
  }
  expect(within(results).getByRole('group', { name: 'Excluded pairs' })).toHaveTextContent('2')
  expect(within(results).getByText(/not establish causation/)).toBeInTheDocument()
  expect(request).toHaveBeenCalledWith('run', { baseline_group_id: 'base', attack_group_id: 'attack' }, expect.any(AbortSignal))
})
test('shows unavailable rates when there are no decided pairs', async () => {
  request.mockResolvedValue({ ...report, decided_pairs: 0, excluded_pairs: 6,
    baseline_success_rate: null, attack_success_rate: null, paired_difference: null })
  mount(); await compare()
  const results = await screen.findByRole('region', { name: 'Paired comparison' })
  expect(within(results).getByRole('group', { name: 'Baseline success rate' })).toHaveTextContent('Unavailable')
  expect(within(results).getByRole('group', { name: 'Paired difference' })).toHaveTextContent('Unavailable')
})
test('suppresses late results when a changed selection cancels a request', async () => {
  let resolve!: (value: PairedOutcomeStatistics) => void
  request.mockReturnValue(new Promise<PairedOutcomeStatistics>((done) => { resolve = done }))
  mount()
  const user = await compare()
  const signal = request.mock.calls[0][2] as AbortSignal
  await user.selectOptions(screen.getByLabelText('Attack group'), 'other')
  expect(signal.aborted).toBe(true)
  await act(async () => { resolve(report) })
  expect(screen.queryByRole('region', { name: 'Paired comparison' })).not.toBeInTheDocument()
})
test('recovers from API errors and clears old results on refresh failure', async () => {
  request.mockRejectedValueOnce(new Error('Scoring expectations are incompatible.'))
  mount()
  const user = await compare()
  expect(await screen.findByText(/Scoring expectations are incompatible/)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Compare outcomes' }))
  await screen.findByRole('region', { name: 'Paired comparison' })
  request.mockRejectedValueOnce(new Error('Connection lost'))
  await user.click(screen.getByRole('button', { name: 'Compare outcomes' }))
  await screen.findByText(/Connection lost/)
  expect(screen.queryByRole('region', { name: 'Paired comparison' })).not.toBeInTheDocument()
})
test.each([
  { status: 'IN_PROGRESS' as const, plan, message: /Complete the run/ },
  { status: 'FAILED' as const, plan, message: /Complete the run/ },
  { status: 'COMPLETED' as const, plan: null, message: /saved execution plan/ },
  { status: 'COMPLETED' as const, plan: { ...plan, atomic_groups: plan.atomic_groups.slice(1) }, message: /baseline and an attack group/ },
])('explains unavailable comparisons without requesting results: $status', ({ message, ...props }) => {
  mount(props)
  expect(screen.getByText(message)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Compare outcomes' })).not.toBeInTheDocument()
  expect(request).not.toHaveBeenCalled()
})
test('cancels requests on unmount', async () => {
  request.mockReturnValue(new Promise(() => {}))
  const view = mount(); await compare()
  const signal = request.mock.calls[0][2] as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
})
