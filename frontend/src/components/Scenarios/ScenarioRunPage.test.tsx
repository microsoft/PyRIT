import { act } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { scenariosApi } from '@/services/api'
import type { ScenarioProgressResult, ScenarioProgressSummary, ScenarioRunPlan, ScenarioRunSummary } from '@/types'
import { scenarioRunAttackRoutePath, scenarioRunRoutePath } from '@/utils/routeParams'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE, type ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import ScenarioRunPage from './ScenarioRunPage'

jest.mock('@/hooks/useScenarioRunProgress', () => ({ useScenarioRunProgress: jest.fn() }))
jest.mock('@/hooks/useScenarioQueue', () => ({ useScenarioQueue: jest.fn() }))
jest.mock('@/services/api', () => ({ scenariosApi: {
  cancelRun: jest.fn(), resumeRun: jest.fn(), getLaunchConfiguration: jest.fn(), startRun: jest.fn(), getQueue: jest.fn(),
  getRunHierarchy: jest.fn(() => new Promise(() => {})), getRunHierarchyNode: jest.fn(),
} }))

const mockUseScenarioQueue = useScenarioQueue as jest.Mock
const queueRetry = jest.fn()
const RUN_ID = '123e4567-e89b-12d3-a456-426614174000'
const SCORER = {
  component_name: 'TrueFalseCompositeScorer', parameters: { score_aggregator: 'AND' },
  children: { sub_scorers: [{
    component_name: 'TrueFalseCompositeScorer', parameters: { score_aggregator: 'OR' },
    children: { sub_scorers: [{ component_name: 'SubStringScorer', parameters: { substring: 'example' }, children: {} }] },
  }] },
}
const TECHNIQUE = { component_name: 'AttackTechnique', parameters: { name: 'direct' }, children: {} }
const SEEDS = [{ component_name: 'SeedObjective', parameters: { dataset_name: 'review', value: 'First objective' }, children: {} }]
const PLAN: ScenarioRunPlan = {
  version: 1, scenario_registry_name: 'test.scenario',
  atomic_groups: [{ id: 'group', atomic_attack_name: 'PromptSendingAttack', display_group: 'Direct', technique_name: 'direct', technique_eval_hash: 'hash', seed_group_ids: ['seed-1', 'seed-2'] }],
  seed_groups: [
    { id: 'seed-1', objective_sha256: 'sha1', objective: 'First objective', prompts: [{ value: 'First seed prompt', sequence: 0, role: 'user', data_type: 'text', parameters: [] }] },
    { id: 'seed-2', objective_sha256: 'sha2', objective: 'Second objective', prompts: [] },
  ],
}
const ATTEMPT: ScenarioProgressResult = {
  attack_result_id: 'attempt-1', conversation_id: 'conversation-1', atomic_group_id: 'group',
  atomic_attack_name: 'PromptSendingAttack', seed_group_id: 'seed-1', outcome: 'success',
  execution_time_ms: 5_000, timestamp: '2026-01-01T00:00:05Z', total_retries: 1, retries: [],
  seed_identifiers: SEEDS,
  score: { scorer_name: 'objective', score_type: 'true_false', status: 'complete', score_value: 'true', score_rationale: 'The objective was met.' },
}
const COUNTS = { completed: 2, planned: 2, succeeded: 1, success_percentage: 50, errors: 0, retries: 1 }
const SUMMARY: ScenarioProgressSummary = {
  overall: COUNTS, objective_scorer: SCORER,
  techniques: [{ ...COUNTS, id: 'direct', display_group: 'Direct', atomic_attack_names: ['PromptSendingAttack'], atomic_group_ids: ['group'], tags: [] }],
  seed_groups: PLAN.seed_groups.map((seed, index) => ({
    ...COUNTS, id: seed.id, objective: seed.objective, completed: 1, planned: 1,
    succeeded: index === 0 ? 1 : 0, success_percentage: index === 0 ? 100 : 0,
  })),
  atomic_groups: [{ ...COUNTS, id: 'group', atomic_attack_name: 'PromptSendingAttack', display_group: 'Direct', status: 'RUNNING', technique_details: TECHNIQUE }],
}
const RUN = {
  scenario_result_id: RUN_ID, scenario_name: 'TestScenario', scenario_registry_name: 'test.scenario',
  scenario_version: 1, status: 'IN_PROGRESS' as const, created_at: '2026-01-01T00:00:00Z',
  started_at: '2026-01-01T00:00:01Z', description: 'A saved scenario description.',
  operation_id: '123e4567-e89b-12d3-a456-426614174001', operation_name: 'Review operation',
  target: { target_type: 'OpenAIChatTarget', model_name: 'review-model' },
  labels: { campaign: 'review' }, scenario_identity: TECHNIQUE,
}
const retry = jest.fn()
const applyRunSummary = jest.fn()
let state: ScenarioRunProgressState

function makeState(overrides: Partial<ScenarioRunProgressState> = {}): ScenarioRunProgressState {
  return {
    ...INITIAL_SCENARIO_RUN_PROGRESS_STATE, loadStatus: 'ready', run: RUN, plan: PLAN, summary: SUMMARY,
    planComplete: true, results: [ATTEMPT, { ...ATTEMPT, attack_result_id: 'attempt-2', conversation_id: 'conversation-2', seed_group_id: 'seed-2', outcome: 'failure' }],
    ...overrides,
  }
}

function runSummary(status: ScenarioRunSummary['status']): ScenarioRunSummary {
  return {
    ...RUN, status, updated_at: RUN.created_at, techniques_used: ['direct'], total_attacks: 2,
    completed_attacks: 2, objective_achieved_rate: 50, failed_attacks: [], attack_retries: [], total_retries: 1,
  }
}

function LocationTrace() {
  const location = useLocation()
  return <output aria-label="Location">{location.pathname}{location.search}</output>
}

function Wrapper({ path, navigationState }: { path: string; navigationState?: object }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={[{ pathname: path, state: navigationState }]}>
    <LocationTrace /><Routes>
      <Route path="/scanner/history/:scenarioResultId/:attackResultId" element={<ScenarioRunPage />} />
      <Route path="/scanner/history/:scenarioResultId" element={<ScenarioRunPage />} />
      <Route path="*" element={<h1>Destination</h1>} />
    </Routes>
  </MemoryRouter></FluentProvider>
}

async function expand(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const toggle = screen.getByRole('button', { name: /^(Expand|Collapse) attacks in Direct$/ })
  if (toggle.getAttribute('aria-expanded') === 'false') await user.click(toggle)
}

beforeEach(() => {
  jest.clearAllMocks()
  mockUseScenarioQueue.mockReturnValue({
    snapshot: { revision: 0, snapshot_at: '2026-01-01T00:00:00Z', active: null, queued: [] },
    loading: false,
    stale: false,
    error: null,
    retry: queueRetry,
  })
  state = makeState()
  jest.mocked(useScenarioRunProgress).mockImplementation(() => ({ state, retry, applyRunSummary }))
})

describe('ScenarioRunPage', () => {
  it('puts conversations and compact results before the saved scenario identity', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByRole('heading', { name: 'test.scenario' })).toBeInTheDocument()
    expect(screen.getByText(RUN.description)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Review operation' })).toHaveAttribute('href', `/operations/${RUN.operation_id}`)
    expect(screen.getByText('review-model')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Atomic attack groups', 'Datasets & objectives', 'Scenario configuration',
    ])
    expect(screen.queryByText(/run id|persisted, secret-free|paired outcomes|overall progress/i)).not.toBeInTheDocument()
    expect(scenariosApi.getQueue).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: 'View queue' })).toHaveAttribute('href', '/scanner/queue')
    await expand(user)
    expect(screen.getByRole('columnheader', { name: 'Duration' })).toBeInTheDocument()
    expect(screen.queryByRole('columnheader', { name: 'Timestamp' })).not.toBeInTheDocument()
    expect(screen.getByText('True', { exact: true })).toBeInTheDocument()
    expect(screen.getByText('False', { exact: true })).toBeInTheDocument()
  })

  it('preserves per-group expansion through polling and completion without collapse text', async () => {
    const user = userEvent.setup()
    const view = render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    screen.getByRole('button', { name: 'Collapse attacks in Direct' }).focus()
    await user.keyboard('{Enter}')
    screen.getByRole('button', { name: 'Expand attacks in Direct' }).focus()
    await user.keyboard('{Enter}')
    expect(screen.getByRole('button', { name: 'Collapse attacks in Direct' })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.queryByText('Collapse', { exact: true })).not.toBeInTheDocument()
    state = makeState({ run: { ...RUN, status: 'COMPLETED', completed_at: '2026-01-01T00:00:10Z' } })
    view.rerender(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByRole('button', { name: 'Collapse attacks in Direct' })).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Cancel', exact: true })).not.toBeInTheDocument()
  })

  it('opens identity-only details by keyboard and restores focus on close', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await expand(user)
    const details = screen.getAllByRole('button', { name: /^Open details for PromptSendingAttack:/ })[0]
    details.focus()
    await user.keyboard('{Enter}')
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.queryByText(/total duration/i)).not.toBeInTheDocument()
    expect(dialog.getByRole('link', { name: 'Open conversation' })).toBeInTheDocument()
    await user.click(dialog.getByRole('button', { name: 'Identity for direct' }))
    expect(JSON.parse(dialog.getByRole('region', { name: 'Identity for direct' }).textContent ?? '')).toEqual(TECHNIQUE)
    await user.click(dialog.getByRole('button', { name: 'Identity for TrueFalseCompositeScorer' }))
    const scorer = JSON.parse(dialog.getByRole('region', { name: 'Identity for TrueFalseCompositeScorer' }).textContent ?? '')
    expect(scorer.children.sub_scorers[0].parameters.score_aggregator).toBe('OR')
    await user.click(dialog.getByRole('button', { name: 'Identity for review' }))
    expect(JSON.parse(dialog.getByRole('region', { name: 'Identity for review' }).textContent ?? '')).toEqual(SEEDS)
    expect(dialog.queryByText('Configuration', { exact: true })).not.toBeInTheDocument()
    await user.click(dialog.getByRole('button', { name: 'Close attack details' }))
    await waitFor(() => expect(details).toHaveFocus())
  })

  it('keeps canonical conversation navigation separate from row details', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await expand(user)
    await user.click(screen.getAllByRole('link', { name: 'Conversation', exact: true })[0])
    expect(screen.getByLabelText('Location')).toHaveTextContent(`/attack/chat/attempt-2/conversations/conversation-2?scenarioResultId=${RUN_ID}`)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('shows the objective, seed prompt group, and its own atomic attacks', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'First objective' }))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getByText('First seed prompt')).toBeInTheDocument()
    expect(dialog.getByRole('heading', { name: 'Atomic attacks' })).toBeInTheDocument()
    expect(dialog.getAllByRole('button', { name: /^Open details for PromptSendingAttack:/ })).toHaveLength(1)
    await user.click(dialog.getByRole('button', { name: /^Open details for PromptSendingAttack:/ }))
    expect(await screen.findByRole('button', { name: 'Close attack details' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close attack details' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'First objective' })).toHaveFocus())
  })

  it('shows per-group and per-objective success rates without restoring paired outcomes', () => {
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(within(screen.getByLabelText('Scan summary')).getByText('50%', { exact: true })).toBeInTheDocument()
    const objectives = within(screen.getByRole('table', { name: 'Datasets and objectives' }))
    expect(objectives.getByRole('row', { name: /First objective/ })).toHaveTextContent('100%')
    expect(objectives.getByRole('row', { name: /Second objective/ })).toHaveTextContent('0%')
    expect(screen.queryByRole('heading', { name: 'Paired outcomes' })).not.toBeInTheDocument()
  })

  it('keeps interrupted saved conversations reachable without inventing an outcome row', () => {
    state = makeState({
      results: [], activeConversations: [],
      recordedConversations: [{
        attack_result_id: 'interrupted', conversation_id: 'saved', atomic_attack_name: 'direct',
        objective: 'First objective', started_at: RUN.started_at, seed_identifiers: [],
      }],
    })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByRole('link', { name: 'Open saved conversation' })).toHaveAttribute(
      'href', `/scanner/history/${RUN_ID}/conversations/saved`,
    )
    expect(screen.queryByRole('row', { name: /View details for/ })).not.toBeInTheDocument()
  })

  it('keeps elapsed-clock updates local instead of rebuilding result groups every second', async () => {
    jest.useFakeTimers()
    jest.setSystemTime(new Date('2026-01-01T00:00:02Z'))
    try {
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      expect(screen.getByText('1s elapsed')).toBeInTheDocument()
      const renders = jest.mocked(useScenarioRunProgress).mock.calls.length
      await act(async () => { jest.advanceTimersByTime(3_000) })
      expect(screen.getByText('4s elapsed')).toBeInTheDocument()
      expect(jest.mocked(useScenarioRunProgress).mock.calls.length).toBe(renders)
    } finally {
      jest.useRealTimers()
    }
  })

  it('announces a terminal transition through the existing live region', () => {
    const view = render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const announcement = within(screen.getByTestId('scenario-run-page')).getByRole('status', { name: 'Run status updates' })
    expect(announcement).toBeEmptyDOMElement()
    state = makeState({ run: { ...RUN, status: 'COMPLETED', completed_at: '2026-01-01T00:00:10Z' } })
    view.rerender(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(announcement).toHaveTextContent('test.scenario: completed.')
  })

  it('shows only conditional operational facts with compact optional overload details', async () => {
    const user = userEvent.setup()
    state = makeState({
      plan: null, planComplete: false,
      summary: { ...SUMMARY, unattributed_attempts: 3 },
      overloadSummaries: [{
        component_role: 'objective_target', count: 2, rate_limit_count: 2, server_error_count: 0,
        status_codes: [429], latest_timestamp: '2026-01-01T00:00:10Z',
      }],
    })
    const view = render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const notice = within(screen.getByLabelText('Scan notices'))
    expect(notice.getByText(/Service overload detected/)).toBeInTheDocument()
    expect(notice.getByText(/3 executions could not be matched/)).toBeInTheDocument()
    expect(notice.getByText(/saved execution plan is incomplete/)).toBeInTheDocument()
    expect(notice.queryByText(/HTTP 429/)).not.toBeInTheDocument()
    await user.click(notice.getByRole('button', { name: 'Overload details' }))
    expect(notice.getByText('objective target: 2 (HTTP 429)')).toBeInTheDocument()
    state = makeState()
    view.rerender(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.queryByLabelText('Scan notices')).not.toBeInTheDocument()
  })

  it('shows the stored scenario identity without a field grid', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} navigationState={{ fromScenarioHistory: true }} />)
    await user.click(screen.getByRole('button', { name: 'Identity for test.scenario', exact: true }))
    expect(screen.getByText(JSON.stringify(TECHNIQUE, null, 2), { exact: false, normalizer: (text: string) => text })).toBeInTheDocument()
    expect(screen.queryByText('Executor', { exact: true })).not.toBeInTheDocument()
  })

  it('opens a saved configuration on the real launch route without starting work', async () => {
    const user = userEvent.setup()
    jest.mocked(scenariosApi.getLaunchConfiguration).mockResolvedValue({ scenario_name: 'test.scenario', target_name: 'original' })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Rerun' }))
    expect(scenariosApi.getLaunchConfiguration).toHaveBeenCalledWith(RUN_ID)
    await waitFor(() => expect(screen.getByLabelText('Location')).toHaveTextContent(`/scanner/launch/test.scenario?rerun=${RUN_ID}`))
    expect(scenariosApi.startRun).not.toHaveBeenCalled()
  })

  it('reports an unavailable saved configuration instead of substituting defaults', async () => {
    const user = userEvent.setup()
    jest.mocked(scenariosApi.getLaunchConfiguration).mockRejectedValue(new Error('Saved configuration is unavailable'))
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Rerun' }))
    expect(await screen.findByText('Saved configuration is unavailable')).toBeInTheDocument()
    expect(screen.getByLabelText('Location')).toHaveTextContent(scenarioRunRoutePath(RUN_ID))
    expect(scenariosApi.startRun).not.toHaveBeenCalled()
  })

  it('cancels only after confirmation and applies the returned run state', async () => {
    const user = userEvent.setup()
    const cancelled = runSummary('CANCELLED')
    jest.mocked(scenariosApi.cancelRun).mockResolvedValue(cancelled)
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Cancel', exact: true }))
    expect(scenariosApi.cancelRun).not.toHaveBeenCalled()
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel scan' }))
    await waitFor(() => expect(applyRunSummary).toHaveBeenCalledWith(cancelled))
    expect(scenariosApi.cancelRun).toHaveBeenCalledWith(RUN_ID)
  })

  it('keeps cancellation errors inside the confirmation', async () => {
    const user = userEvent.setup()
    jest.mocked(scenariosApi.cancelRun).mockRejectedValue(new Error('Already stopped'))
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Cancel', exact: true }))
    await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel scan' }))
    expect(await within(screen.getByRole('dialog')).findByText('Already stopped')).toBeInTheDocument()
    expect(applyRunSummary).not.toHaveBeenCalled()
  })

  it('keeps scan diagnostics available without filling the result page with execution details', async () => {
    const user = userEvent.setup()
    state = makeState({ run: { ...RUN, status: 'FAILED', error: 'Detailed execution diagnostics' } })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByText('Scan failed. Resume to continue remaining attacks.')).toBeInTheDocument()
    expect(screen.queryByText('Detailed execution diagnostics')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Error details' }))
    expect(screen.getByText('Detailed execution diagnostics')).toBeVisible()
  })

  it('does not describe a cancellation with no error outcomes as an attack error', () => {
    state = makeState({ run: { ...RUN, status: 'CANCELLED', error: 'Run was cancelled by user' } })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByText('Scan cancelled.')).toBeInTheDocument()
    expect(screen.queryByText(/0 attacks ended with an error/)).not.toBeInTheDocument()
  })

  it.each(['FAILED', 'CANCELLED'] as const)('resumes the original %s run rather than launching a new one', async (status) => {
    const user = userEvent.setup()
    state = makeState({ run: { ...RUN, status } })
    const resumed = runSummary('IN_PROGRESS')
    jest.mocked(scenariosApi.resumeRun).mockResolvedValue(resumed)
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} navigationState={{ fromScenarioHistory: true }} />)
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    await waitFor(() => expect(applyRunSummary).toHaveBeenCalledWith(resumed))
    expect(scenariosApi.resumeRun).toHaveBeenCalledWith(RUN_ID)
    expect(scenariosApi.startRun).not.toHaveBeenCalled()
  })

  it.each([400, 404, 409, 500])('surfaces HTTP %s resume errors and retains existing progress', async (status: number) => {
    const user = userEvent.setup()
    state = makeState({ run: { ...RUN, status: 'FAILED' } })
    jest.mocked(scenariosApi.resumeRun).mockRejectedValue({ isAxiosError: true, response: { status, data: { detail: 'Resume rejected' } } })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    expect(await screen.findByText('Resume rejected')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Scan summary')).getByText('50%', { exact: true })).toBeInTheDocument()
    expect(retry).toHaveBeenCalled()
  })

  it('does not apply a late resume after leaving the scan', async () => {
    const user = userEvent.setup()
    let resolve: (value: ScenarioRunSummary) => void = () => {}
    jest.mocked(scenariosApi.resumeRun).mockReturnValue(new Promise((complete) => { resolve = complete }))
    state = makeState({ run: { ...RUN, status: 'FAILED' } })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} navigationState={{ fromScenarioHistory: true }} />)
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    await user.click(screen.getByRole('link', { name: 'Back to scans' }))
    await act(async () => { resolve(runSummary('IN_PROGRESS')) })
    expect(applyRunSummary).not.toHaveBeenCalled()
  })

  it('shows live conversations without counting them as completed results', async () => {
    state = makeState({ results: [], activeConversations: [{
      attack_result_id: 'live', conversation_id: 'live-conversation', atomic_attack_name: 'Live attack',
      objective: 'Live objective', seed_group_id: 'seed-1', started_at: RUN.created_at, seed_identifiers: SEEDS,
    }] })
    render(<Wrapper path={scenarioRunAttackRoutePath(RUN_ID, 'live')} />)
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByRole('link', { name: 'Watch live conversation' })).toHaveAttribute('href', `/scanner/history/${RUN_ID}/conversations/live-conversation`)
    expect(dialog.getByText('In progress')).toBeInTheDocument()
    expect(within(screen.getByLabelText('Scan summary')).getByText('50%', { exact: true })).toBeInTheDocument()
  })

  it('retains explicit stale recovery and previously loaded data', async () => {
    const user = userEvent.setup()
    state = makeState({ stale: true, error: 'Connection lost' })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByRole('heading', { name: 'test.scenario' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry', exact: true }))
    expect(retry).toHaveBeenCalledTimes(1)
  })

  it.each(['loading', 'not-found', 'error'] as const)('renders the %s state with recovery', (loadStatus) => {
    state = makeState({ run: null, summary: null, loadStatus, error: loadStatus === 'loading' ? null : 'Scan unavailable' })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    if (loadStatus === 'loading') expect(screen.getByText('Loading scan...')).toBeInTheDocument()
    else expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
  })

  it('retains history filters and transient suite return navigation', () => {
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} navigationState={{ suiteExecutionId: 'suite', suiteName: 'Review suite' }} />)
    expect(screen.getByRole('link', { name: 'Back to Review suite' })).toHaveAttribute('href', '/scanner/history/suites/executions/suite')
  })
})
