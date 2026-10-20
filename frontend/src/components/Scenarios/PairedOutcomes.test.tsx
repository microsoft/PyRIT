import { act, render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'
import { scenariosApi } from '@/services/api'
import type { PairedOutcomeDetail, PairedOutcomeDetailPage, PairedOutcomeStatistics, ScenarioRunPlan } from '@/types'
import PairedOutcomes from './PairedOutcomes'

jest.mock('@/services/api', () => ({ scenariosApi: { getPairedOutcomes: jest.fn(), getPairedOutcomeDetails: jest.fn() } }))
const request = scenariosApi.getPairedOutcomes as jest.Mock
const detailsRequest = scenariosApi.getPairedOutcomeDetails as jest.Mock
const runId = '00000000-0000-0000-0000-00000000238c'
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
  return render(
    <MemoryRouter>
      <FluentProvider theme={webLightTheme}>
        <PairedOutcomes scenarioResultId={runId} status="COMPLETED" plan={plan} {...props} />
      </FluentProvider>
    </MemoryRouter>,
  )
}
async function compare() {
  const user = userEvent.setup()
  await user.selectOptions(screen.getByLabelText('Baseline group'), 'base')
  await user.selectOptions(screen.getByLabelText('Attack group'), 'attack')
  await user.click(screen.getByRole('button', { name: 'Compare outcomes' }))
  return user
}
const pair: PairedOutcomeDetail = {
  seed_group_id: 'seed-1', objective: 'Ask a clear question',
  seed_prompts: [{ sequence: 1, value: 'Example prompt', data_type: 'text', role: 'user', parameters: [] }],
  baseline: {
    outcome: 'failure', attack_result_id: 'baseline-result', conversation_id: 'baseline-conversation',
    outcome_reason: 'Baseline failed', timestamp: '2026-10-20T00:00:00Z',
  },
  attack: {
    outcome: 'success', attack_result_id: 'attack-result', conversation_id: 'attack-conversation',
    outcome_reason: null, timestamp: '2026-10-20T00:01:00Z',
  },
}
function detailPage(pairs: PairedOutcomeDetail[] = [pair], next_cursor: string | null = null,
  total_pairs = 1): PairedOutcomeDetailPage {
  return {
    baseline_group_id: 'base', attack_group_id: 'attack', transition: 'failure_to_success',
    pairs, next_cursor, total_pairs,
  }
}
beforeEach(() => {
  request.mockReset(); request.mockResolvedValue(report)
  detailsRequest.mockReset(); detailsRequest.mockResolvedValue(detailPage())
})

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
  expect(request).toHaveBeenCalledWith(runId, { baseline_group_id: 'base', attack_group_id: 'attack' }, expect.any(AbortSignal))
  expect(detailsRequest).not.toHaveBeenCalled()
})
test('expands on keyboard, lazily retrieves the selected transition, and links both conversations in Scenario context', async () => {
  mount()
  const user = await compare()
  const expand = await screen.findByRole('button', { name: 'Failure → Success' })
  expect(expand).toHaveAttribute('aria-expanded', 'false')
  expect(detailsRequest).not.toHaveBeenCalled()
  expand.focus()
  await user.keyboard('{Enter}')
  expect(expand).toHaveAttribute('aria-expanded', 'true')
  const details = await screen.findByRole('region', { name: 'Failure → Success pair details' })
  expect(within(details).getByText('Ask a clear question')).toBeInTheDocument()
  expect(within(details).getByText('Logical seed: seed-1')).toBeInTheDocument()
  expect(within(details).getByText('Example prompt')).toBeInTheDocument()
  expect(within(details).getByText('Outcome: failure')).toBeInTheDocument()
  expect(within(details).getByText('Outcome: success')).toBeInTheDocument()
  expect(within(details).getByText('Baseline failed')).toBeInTheDocument()
  expect(within(details).getByRole('link', { name: 'Open baseline conversation' }))
    .toHaveAttribute('href', `/attacks/baseline-result/conversations/baseline-conversation?scenarioResultId=${runId}`)
  expect(within(details).getByRole('link', { name: 'Open attack conversation' }))
    .toHaveAttribute('href', `/attacks/attack-result/conversations/attack-conversation?scenarioResultId=${runId}`)
  expect(detailsRequest).toHaveBeenCalledWith(runId, {
    baseline_group_id: 'base', attack_group_id: 'attack', transition: 'failure_to_success', limit: 25,
  }, expect.any(AbortSignal))
  const firstSignal = detailsRequest.mock.calls[0][2] as AbortSignal
  await user.click(expand)
  expect(expand).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByRole('region', { name: 'Failure → Success pair details' })).not.toBeInTheDocument()
  expect(firstSignal.aborted).toBe(true)
  await user.click(expand)
  expect(detailsRequest).toHaveBeenCalledTimes(2)
  expect(await screen.findByText('Ask a clear question')).toBeInTheDocument()
})

test('paginates with bounded requests, stable cursors and no duplicate pairs', async () => {
  const firstPage = Array.from({ length: 25 }, (_, index) => ({
    ...pair, seed_group_id: `seed-${String(index + 1).padStart(2, '0')}`,
  }))
  const secondPage = Array.from({ length: 6 }, (_, index) => ({
    ...pair, seed_group_id: `seed-${index + 26}`,
  }))
  request.mockResolvedValue({ ...report, failure_to_success: 31, decided_pairs: 34 })
  detailsRequest.mockResolvedValueOnce(detailPage(firstPage, 'opaque-page-1', 31))
    .mockResolvedValueOnce(detailPage(secondPage, null, 31))
  mount()
  const user = await compare()
  await user.click(await screen.findByRole('button', { name: 'Failure → Success' }))
  expect(await screen.findByText('25 of 31 pairs shown')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Load more Failure → Success pairs' }))
  expect(await screen.findByText('31 of 31 pairs shown')).toBeInTheDocument()
  expect(screen.getAllByText('Logical seed: seed-01')).toHaveLength(1)
  expect(screen.queryByRole('button', { name: 'Load more Failure → Success pairs' })).not.toBeInTheDocument()
  expect(detailsRequest).toHaveBeenNthCalledWith(2, runId, {
    baseline_group_id: 'base', attack_group_id: 'attack', transition: 'failure_to_success',
    cursor: 'opaque-page-1', limit: 25,
  }, expect.any(AbortSignal))
})

test('retries a failed first page and a failed additional page without losing loaded pairs', async () => {
  request.mockResolvedValue({ ...report, failure_to_success: 2, decided_pairs: 5 })
  detailsRequest.mockRejectedValueOnce(new Error('Connection lost'))
    .mockResolvedValueOnce(detailPage([pair], 'page-2', 2))
    .mockRejectedValueOnce(new Error('Second page unavailable'))
    .mockResolvedValueOnce(detailPage([{ ...pair, seed_group_id: 'seed-2' }], null, 2))
  mount()
  const user = await compare()
  await user.click(await screen.findByRole('button', { name: 'Failure → Success' }))
  expect(await screen.findByText('Connection lost')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry Failure → Success pairs' }))
  expect(await screen.findByText('1 of 2 pairs shown')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Load more Failure → Success pairs' }))
  expect(await screen.findByText('Second page unavailable')).toBeInTheDocument()
  expect(screen.getByText('Logical seed: seed-1')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry Failure → Success pairs' }))
  expect(await screen.findByText('2 of 2 pairs shown')).toBeInTheDocument()
  expect(screen.getAllByText('Logical seed: seed-1')).toHaveLength(1)
  expect(detailsRequest.mock.calls[2][1]).toMatchObject({ cursor: 'page-2' })
  expect(detailsRequest.mock.calls[3][1]).toMatchObject({ cursor: 'page-2' })
})

test('zero-count transitions show empty state without making a detail request', async () => {
  request.mockResolvedValue({ ...report, failure_to_success: 0, decided_pairs: 3 })
  mount()
  const user = await compare()
  await user.click(await screen.findByRole('button', { name: 'Failure → Success' }))
  expect(screen.getByText('No matching pairs in this transition.')).toBeInTheDocument()
  expect(detailsRequest).not.toHaveBeenCalled()
})

test('collapse cancels requests and ignores late responses, then re-expansion starts fresh', async () => {
  let resolve!: (value: PairedOutcomeDetailPage) => void
  detailsRequest.mockReturnValueOnce(new Promise<PairedOutcomeDetailPage>((done) => { resolve = done }))
  mount()
  const user = await compare()
  const expand = await screen.findByRole('button', { name: 'Failure → Success' })
  await user.click(expand)
  const signal = detailsRequest.mock.calls[0][2] as AbortSignal
  await user.click(expand)
  expect(signal.aborted).toBe(true)
  await act(async () => resolve(detailPage()))
  expect(screen.queryByText('Ask a clear question')).not.toBeInTheDocument()
  await user.click(expand)
  expect(detailsRequest).toHaveBeenCalledTimes(2)
  expect(await screen.findByText('Ask a clear question')).toBeInTheDocument()
})

test('selection changes cancel detail requests and suppress stale pairs', async () => {
  let resolve!: (value: PairedOutcomeDetailPage) => void
  detailsRequest.mockReturnValueOnce(new Promise<PairedOutcomeDetailPage>((done) => { resolve = done }))
  mount()
  const user = await compare()
  await user.click(await screen.findByRole('button', { name: 'Failure → Success' }))
  const signal = detailsRequest.mock.calls[0][2] as AbortSignal
  await user.selectOptions(screen.getByLabelText('Attack group'), 'other')
  expect(signal.aborted).toBe(true)
  await act(async () => resolve(detailPage()))
  expect(screen.queryByRole('region', { name: 'Paired comparison' })).not.toBeInTheDocument()
  expect(screen.queryByText('Ask a clear question')).not.toBeInTheDocument()
})

test('rejects an inconsistent detail count instead of presenting mismatched observations', async () => {
  detailsRequest.mockResolvedValueOnce(detailPage([pair], null, 99))
  mount()
  const user = await compare()
  await user.click(await screen.findByRole('button', { name: 'Failure → Success' }))
  expect(await screen.findByText(/Pair details changed/)).toBeInTheDocument()
  expect(screen.queryByText('Ask a clear question')).not.toBeInTheDocument()
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
