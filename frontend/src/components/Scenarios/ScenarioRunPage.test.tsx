import { act } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'

import { useScenarioQueue } from '@/hooks/useScenarioQueue'
import { useScenarioRunProgress } from '@/hooks/useScenarioRunProgress'
import { scenariosApi } from '@/services/api'
import type {
  ScenarioHierarchyNode, ScenarioHierarchyProjection, ScenarioProgressResult, ScenarioProgressSummary, ScenarioRunPlan, ScenarioRunSummary,
} from '@/types'
import { scenarioRunAttackRoutePath, scenarioRunRoutePath } from '@/utils/routeParams'
import { INITIAL_SCENARIO_RUN_PROGRESS_STATE, type ScenarioRunProgressState } from '@/utils/scenarioRunProgress'

import ScenarioRunPage from './ScenarioRunPage'

jest.mock('@/hooks/useScenarioRunProgress', () => ({ useScenarioRunProgress: jest.fn() }))
jest.mock('@/hooks/useScenarioQueue', () => ({ useScenarioQueue: jest.fn() }))
jest.mock('@/hooks/useAssertPolicyMetrics', () => ({
  useAssertPolicyMetrics: () => ({ status: 'loading', metrics: null, error: null, retry: jest.fn() }),
}))
jest.mock('@/services/api', () => ({ scenariosApi: {
  cancelRun: jest.fn(), resumeRun: jest.fn(), getLaunchConfiguration: jest.fn(), startRun: jest.fn(), getQueue: jest.fn(),
  getRunHierarchy: jest.fn(() => new Promise(() => {})), getRunHierarchyNode: jest.fn(),
  getPairedOutcomes: jest.fn(), getPairedOutcomeDetails: jest.fn(),
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

function LeaveRunButton() {
  const navigate = useNavigate()
  return <button onClick={() => { navigate('/elsewhere') }}>Leave run</button>
}

function OpenAttemptButton() {
  const navigate = useNavigate()
  return <button onClick={() => { navigate(`/scanner/history/${RUN_ID}/attempt-1`) }}>Open attempt</button>
}

function Wrapper({ path, navigationState }: { path: string; navigationState?: object }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={[{ pathname: path, state: navigationState }]}>
    <LocationTrace /><LeaveRunButton /><OpenAttemptButton /><Routes>
      <Route path="/scanner/history/:scenarioResultId/:attackResultId?" element={<ScenarioRunPage />} />
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
  it('keeps comparison optional on completed scans and cancels it when collapsed', async () => {
    const user = userEvent.setup()
    state = makeState({
      run: { ...RUN, status: 'COMPLETED' },
      plan: { ...PLAN, atomic_groups: [
        { ...PLAN.atomic_groups[0], id: 'baseline', kind: 'baseline' },
        { ...PLAN.atomic_groups[0], id: 'attack', kind: 'attack' },
      ] },
    })
    jest.mocked(scenariosApi.getPairedOutcomes).mockReturnValue(new Promise(() => {}))
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const disclosure = screen.getByRole('button', { name: 'Compare baseline and attack' })
    expect(disclosure).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region', { name: 'Paired outcomes', exact: true })).not.toBeInTheDocument()
    expect(scenariosApi.getPairedOutcomes).not.toHaveBeenCalled()
    disclosure.focus()
    await user.keyboard('{Enter}')
    await user.selectOptions(screen.getByLabelText('Baseline group'), 'baseline')
    await user.selectOptions(screen.getByLabelText('Attack group'), 'attack')
    await user.click(screen.getByRole('button', { name: 'Compare outcomes' }))
    expect(scenariosApi.getPairedOutcomes).toHaveBeenCalledWith(
      RUN_ID, { baseline_group_id: 'baseline', attack_group_id: 'attack' }, expect.any(AbortSignal),
    )
    const signal = jest.mocked(scenariosApi.getPairedOutcomes).mock.calls[0][2]
    await user.click(disclosure)
    expect(signal?.aborted).toBe(true)
    expect(screen.queryByRole('region', { name: 'Paired outcomes', exact: true })).not.toBeInTheDocument()
    await user.click(disclosure)
    expect(screen.getByRole('button', { name: 'Compare outcomes' })).toBeDisabled()
  })

  it('does not use a synthesized legacy plan for comparison', async () => {
    const user = userEvent.setup()
    state = makeState({
      run: { ...RUN, status: 'COMPLETED' },
      planComplete: false,
      plan: { ...PLAN, atomic_groups: [
        { ...PLAN.atomic_groups[0], id: 'baseline', kind: 'baseline' },
        { ...PLAN.atomic_groups[0], id: 'attack', kind: 'attack' },
      ] },
    })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Compare baseline and attack' }))
    expect(screen.getByRole('region', { name: 'Paired outcomes', exact: true })).toHaveTextContent(
      'Paired outcomes require a saved execution plan.',
    )
    expect(screen.queryByLabelText('Baseline group')).not.toBeInTheDocument()
    expect(scenariosApi.getPairedOutcomes).not.toHaveBeenCalled()
  })

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

  it('shows ASSERT metrics only for completed foundry.assert_policy runs', () => {
    const completed = { ...RUN, status: 'COMPLETED' as const, completed_at: '2026-01-01T00:00:10Z' }
    state = makeState({ run: { ...completed, scenario_registry_name: 'foundry.assert_policy', scenario_name: 'AssertPolicy' } })
    const view = render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByRole('heading', { name: 'ASSERT policy metrics' })).toBeInTheDocument()

    state = makeState({ run: completed })
    view.rerender(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.queryByRole('heading', { name: 'ASSERT policy metrics' })).not.toBeInTheDocument()
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
    expect(dialog.getAllByText('The objective was met.', { exact: true })).toHaveLength(1)
    expect(dialog.getAllByText('5s', { exact: true })).toHaveLength(1)
    expect(dialog.queryByText(/5s · 1 retries/)).not.toBeInTheDocument()
    expect(dialog.getAllByText('Execution time', { exact: true })).toHaveLength(1)
    expect(dialog.getAllByText('Retries', { exact: true })).toHaveLength(1)
    expect(dialog.getAllByText('Timestamp', { exact: true })).toHaveLength(1)
    expect(dialog.queryByText('Attack Success', { exact: true })).not.toBeInTheDocument()
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

  it('restores focus to the objective opener when the objective dialog closes', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const objective = screen.getByRole('button', { name: 'First objective' })
    await user.click(objective)
    const dialog = within(await screen.findByRole('dialog'))
    await user.click(dialog.getByRole('button', { name: 'Close', exact: true }))
    await waitFor(() => expect(objective).toHaveFocus())
  })

  it('restores focus to the objective opener after Escape and on reopening', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const objective = screen.getByRole('button', { name: 'First objective' })
    await user.click(objective)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(objective).toHaveFocus())
    await user.click(objective)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(objective).toHaveFocus())
  })

  it('restores focus to the technique opener when attack details are dismissed with Escape', async () => {
    const user = userEvent.setup()
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await expand(user)
    const details = screen.getAllByRole('button', { name: /^Open details for PromptSendingAttack:/ })[0]
    await user.click(details)
    await screen.findByRole('dialog')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(details).toHaveFocus())
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

  it('keeps distinct interrupted retry conversations and excludes completed transcripts', () => {
    const saved = {
      attack_result_id: 'attempt-1', conversation_id: 'interrupted-retry',
      atomic_attack_name: 'direct', objective: 'First objective', started_at: RUN.started_at,
      seed_identifiers: [],
    }
    state = makeState({
      recordedConversations: [
        saved, saved,
        { ...saved, conversation_id: 'conversation-1' },
        { ...saved, attack_result_id: 'attempt-2', conversation_id: 'conversation-2' },
      ],
    })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    const savedLinks = screen.getAllByRole('link', { name: 'Open saved conversation' })
    expect(savedLinks).toHaveLength(1)
    expect(savedLinks[0]).toHaveAttribute(
      'href', `/scanner/history/${RUN_ID}/conversations/interrupted-retry`,
    )
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
    expect(scenariosApi.getLaunchConfiguration).toHaveBeenCalledWith(RUN_ID, expect.any(AbortSignal))
    await waitFor(() => expect(screen.getByLabelText('Location')).toHaveTextContent(`/scanner/launch/test.scenario?rerun=${RUN_ID}`))
    expect(scenariosApi.startRun).not.toHaveBeenCalled()
  })

  it('aborts and ignores a late rerun response after leaving the run', async () => {
    const user = userEvent.setup()
    let resolveConfiguration: ((value: Awaited<ReturnType<typeof scenariosApi.getLaunchConfiguration>>) => void) | null = null
    let requestSignal: AbortSignal | undefined
    jest.mocked(scenariosApi.getLaunchConfiguration).mockImplementation((_id, signal) => new Promise((resolve) => {
      resolveConfiguration = resolve
      requestSignal = signal
    }))
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Rerun' }))
    expect(requestSignal).toBeDefined()
    await user.click(screen.getByRole('button', { name: 'Leave run' }))
    expect(requestSignal?.aborted).toBe(true)
    const resolve = resolveConfiguration
    if (!resolve) throw new Error('The launch configuration request did not start.')
    await act(async () => {
      resolve({ scenario_name: 'test.scenario', target_name: 'original' })
    })
    expect(screen.getByLabelText('Location')).toHaveTextContent('/elsewhere')
  })

  it('keeps error details visible once in completed attack details', async () => {
    const user = userEvent.setup()
    state = makeState({
      results: [{
        ...ATTEMPT, outcome: 'error', score: null,
        error_type: 'TimeoutError', error_message: 'The target timed out.',
      }],
    })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await expand(user)
    await user.click(screen.getByRole('button', { name: /^Open details for PromptSendingAttack:/ }))
    const dialog = within(await screen.findByRole('dialog'))
    expect(dialog.getAllByText('TimeoutError: The target timed out.', { exact: true })).toHaveLength(1)
    expect(dialog.queryByRole('button', { name: 'Error details' })).not.toBeInTheDocument()
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

  it('aborts a rerun when the current run route changes to attempt details', async () => {
    const user = userEvent.setup()
    let resolveConfiguration: ((value: Awaited<ReturnType<typeof scenariosApi.getLaunchConfiguration>>) => void) | null = null
    let requestSignal: AbortSignal | undefined
    jest.mocked(scenariosApi.getLaunchConfiguration).mockImplementation((_id, signal) => new Promise((resolve) => {
      resolveConfiguration = resolve
      requestSignal = signal
    }))
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    await user.click(screen.getByRole('button', { name: 'Rerun' }))
    await user.click(screen.getByRole('button', { name: 'Open attempt' }))
    expect(requestSignal?.aborted).toBe(true)
    const resolve = resolveConfiguration
    if (!resolve) throw new Error('The launch configuration request did not start.')
    await act(async () => { resolve({ scenario_name: 'test.scenario', target_name: 'original' }) })
    expect(screen.getByLabelText('Location')).toHaveTextContent(`/scanner/history/${RUN_ID}/attempt-1`)
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

  it('keeps the newest execution in the latest-100 fallback when it belongs to a later member group', () => {
    const olderAttempts = Array.from({ length: 100 }, (_, index) => ({
      ...ATTEMPT, attack_result_id: `attempt-${index + 1}`, conversation_id: `conversation-${index + 1}`,
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, index + 1)).toISOString(),
    }))
    const newest = {
      ...ATTEMPT, attack_result_id: 'attempt-101', conversation_id: 'conversation-101', atomic_group_id: 'group-b',
      atomic_attack_name: 'SecondAttack', timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, 101)).toISOString(),
    }
    state = makeState({
      results: [...olderAttempts, newest],
      summary: { ...SUMMARY, techniques: [{ ...SUMMARY.techniques[0], atomic_group_ids: ['group', 'group-b'], atomic_attack_names: ['PromptSendingAttack', 'SecondAttack'] }] },
    })
    render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
    expect(screen.getByText('Showing the latest 100 of 101 executions in this group.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Open details for SecondAttack/ })).toBeInTheDocument()
  })

  describe('flat-row fallback while result relationships load or fail', () => {
    const attemptAt = (index: number, atomicGroupId: string): ScenarioProgressResult => ({
      ...ATTEMPT, attack_result_id: `attempt-${index}`, conversation_id: `conversation-${index}`,
      atomic_group_id: atomicGroupId, atomic_attack_name: `Attack ${index}`,
      timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString(),
    })
    const cutoffResults = (): ScenarioProgressResult[] => [
      ...Array.from({ length: 100 }, (_, index) => attemptAt(index + 1, 'group')),
      attemptAt(101, 'group-b'),
    ]
    const twoMemberSummary = { ...SUMMARY, techniques: [{ ...SUMMARY.techniques[0], atomic_group_ids: ['group', 'group-b'] }] }
    const shownAttacks = (): (string | undefined)[] => within(screen.getByRole('table', { name: 'Direct attack results' }))
      .getAllByRole('button', { name: /^Open details for / })
      .map((button) => button.getAttribute('aria-label')?.match(/Attack \d+/)?.[0])
    const expectLatestHundred = (): void => {
      expect(screen.getByText('Showing the latest 100 of 101 executions in this group.')).toBeInTheDocument()
      expect(shownAttacks()).toHaveLength(100)
      expect(shownAttacks()[0]).toBe('Attack 101')
      expect(shownAttacks()).not.toContain('Attack 1')
    }

    it('orders interleaved member groups newest-first while relationships load', () => {
      state = makeState({
        results: [attemptAt(1, 'group'), attemptAt(2, 'group-b'), attemptAt(3, 'group'), attemptAt(4, 'group-b')],
        summary: twoMemberSummary,
      })
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      expect(shownAttacks()).toEqual(['Attack 4', 'Attack 3', 'Attack 2', 'Attack 1'])
    })

    it('keeps the newest 100 at the cutoff while relationships load', () => {
      state = makeState({ results: cutoffResults(), summary: twoMemberSummary })
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      expect(screen.getByText(/Loading result relationships/)).toBeInTheDocument()
      expectLatestHundred()
    })

    it('keeps the newest 100 at the cutoff after relationships fail to load', async () => {
      jest.mocked(scenariosApi.getRunHierarchy).mockRejectedValueOnce(new Error('Relationship service unavailable'))
      state = makeState({ results: cutoffResults(), summary: twoMemberSummary })
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      expect(await screen.findByText(/Could not load the result relationships/)).toBeInTheDocument()
      expectLatestHundred()
    })

    it('keeps a one-member group at its newest 100 while relationships load', () => {
      state = makeState({ results: Array.from({ length: 101 }, (_, index) => attemptAt(index + 1, 'group')) })
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      expect(shownAttacks()).toHaveLength(100)
      expect(shownAttacks()[0]).toBe('Attack 101')
      expect(shownAttacks()).not.toContain('Attack 1')
    })

    it('shows every supplied root without the fallback notice once relationships are ready', async () => {
      const roots = Array.from({ length: 101 }, (_, index): ScenarioHierarchyNode => ({
        attack_result_id: `attempt-${index + 1}`, conversation_id: `conversation-${index + 1}`, result_role: 'target_facing',
        atomic_attack_name: `Attack ${index + 1}`, atomic_group_id: 'group', seed_group_id: 'seed-1', objective: 'First objective',
        outcome: 'success', execution_time_ms: 5_000, executed_turns: 1,
        timestamp: new Date(Date.UTC(2026, 0, 1, 0, 0, index + 1)).toISOString(),
        total_retries: 0, retries: [], retries_truncated: false, child_attack_result_ids: [], child_references_truncated: false,
        children: [], children_truncated: false, child_offset: 0, unresolved_child_attack_result_ids: [],
        unresolved_child_references_truncated: false, conversations: [], conversations_truncated: false,
      }))
      jest.mocked(scenariosApi.getRunHierarchy).mockResolvedValueOnce({
        scenario_result_id: RUN_ID, scenario_name: 'TestScenario', scenario_version: 1, status: 'COMPLETED',
        groups: [{ atomic_group_id: 'group', atomic_attack_name: 'PromptSendingAttack', display_group: 'Direct', roots, counts: COUNTS }],
        overall_counts: COUNTS,
        diagnostics: {
          failed_attacks: [], failed_attacks_count: 0, failed_attacks_truncated: false, attack_retries: [],
          attack_retries_count: 0, attack_retries_truncated: false, total_retries: 0, rootless_result_ids: [],
          rootless_results_count: 0, rootless_results_truncated: false,
        },
        truncated: false, has_more: false, next_cursor: null,
      } satisfies ScenarioHierarchyProjection)
      state = makeState({ results: cutoffResults(), summary: twoMemberSummary })
      render(<Wrapper path={scenarioRunRoutePath(RUN_ID)} />)
      await waitFor(() => expect(screen.queryByText(/Loading result relationships/)).not.toBeInTheDocument())
      expect(screen.queryByText(/Showing the latest/)).not.toBeInTheDocument()
      expect(shownAttacks()).toHaveLength(101)
      expect(shownAttacks()[0]).toBe('Attack 1')
    })
  })
})
