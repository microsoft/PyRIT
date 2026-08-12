import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { datasetsApi, scenariosApi, targetsApi } from '@/services/api'
import type {
  RegisteredScenario,
  ScenarioDefaultRunSizeEstimate,
  ScenarioRunSizeEstimateRequest,
  TargetInstance,
} from '@/types'

import ScenarioDetail from './ScenarioDetail'

jest.mock('@/services/api', () => ({
  scenariosApi: {
    estimateRun: jest.fn(),
    getScenario: jest.fn(),
    startRun: jest.fn(),
  },
  targetsApi: {
    listTargets: jest.fn(),
  },
  datasetsApi: {
    listDatasets: jest.fn(),
  },
}))

const mockGetScenario = scenariosApi.getScenario as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock
const mockStartRun = scenariosApi.startRun as jest.Mock
const mockListTargets = targetsApi.listTargets as jest.Mock
const mockListDatasets = datasetsApi.listDatasets as jest.Mock
const REMOVED_NORMAL_ESTIMATE_LABELS = new RegExp(
  [
    ['Run', 'size', 'calculated'].join(' '),
    ['Final', 'count', 'set', 'at', 'launch'].join(' '),
  ].join('|'),
  'i',
)
const CORRECT_HIGHLIGHTED_SETTING_MESSAGE = 'Correct the highlighted setting to calculate this run.'

const mockNavigate = jest.fn()
const RAW_IMAGE_HTML = ['<', 'img src=x onerror="alert(1)">'].join('')

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

function makeScenario(overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  const description = overrides.description ?? 'Red teams a target.'
  const defaultTechnique = overrides.default_technique ?? 'default_technique'
  const aggregateTechniques = overrides.aggregate_techniques ?? ['default_technique']
  const defaultTechniques = overrides.default_techniques
    ?? (aggregateTechniques.includes(defaultTechnique) ? ['crescendo'] : [defaultTechnique])
  return {
    scenario_name: 'foundry.red_team_agent',
    scenario_type: 'RedTeamAgentScenario',
    scenario_version: 1,
    aggregate_technique_expansions: overrides.aggregate_technique_expansions
      ?? Object.fromEntries(
        aggregateTechniques.map((name) => [name, name === defaultTechnique ? defaultTechniques : []]),
      ),
    all_techniques: ['default_technique', 'crescendo'],
    technique_summaries: [],
    default_datasets: ['harmbench'],
    dataset_size_limit: {
      default_scope: 'none',
      default_count: null,
      override_scope: 'per_dataset',
    },
    baseline_policy: 'enabled',
    include_baseline_by_default: true,
    supported_parameters: [],
    default_run_size: {
      version: 1,
      status: 'unavailable',
      total_attack_count: null,
      minimum_attack_count: null,
      maximum_attack_count: null,
      condition: null,
      components: [],
      datasets: [],
      adaptive_details: null,
      note: 'Default sizing is unavailable.',
      retries_included: false,
    },
    ...overrides,
    description,
    description_markdown: overrides.description_markdown ?? description,
    default_technique: defaultTechnique,
    default_techniques: defaultTechniques,
    aggregate_techniques: aggregateTechniques,
  }
}

function makeTarget(name: string): TargetInstance {
  return {
    target_registry_name: name,
    identifier: { class_name: 'OpenAIChatTarget', hash: `${name}-hash` },
  }
}

function makeEstimate(
  total: number | null,
  status: ScenarioDefaultRunSizeEstimate['status'] = total === null ? 'conditional' : 'exact',
): ScenarioDefaultRunSizeEstimate {
  return {
    version: 1,
    status,
    total_attack_count: total,
    minimum_attack_count: null,
    maximum_attack_count: null,
    condition: null,
    components: total === null
      ? []
      : [
          {
            label: 'Configured attacks',
            count: total,
            factors: [],
            is_baseline: false,
            note: null,
          },
        ],
    datasets: [],
    adaptive_details: null,
    note: null,
    retries_included: false,
  }
}

function makeAdaptiveScenario(): RegisteredScenario {
  const defaultMembers = ['role_play_movie_script', 'many_shot']
  const defaultDatasets = [
    'airt_hate',
    'airt_fairness',
    'airt_violence',
    'airt_sexual',
    'airt_harassment',
    'airt_misinformation',
    'airt_leakage',
  ]
  const aggregateTechniqueExpansions = {
    default: defaultMembers,
    all: [...defaultMembers, ...Array.from({ length: 15 }, (_, index) => `all_member_${index + 1}`)],
    core: Array.from({ length: 14 }, (_, index) => `core_member_${index + 1}`),
    extra: Array.from({ length: 3 }, (_, index) => `extra_member_${index + 1}`),
    light: Array.from({ length: 9 }, (_, index) => `light_member_${index + 1}`),
    multi_turn: Array.from({ length: 5 }, (_, index) => `multi_turn_member_${index + 1}`),
    single_turn: Array.from({ length: 12 }, (_, index) => `single_turn_member_${index + 1}`),
  }
  return makeScenario({
    scenario_name: 'adaptive.text_adaptive',
    scenario_type: 'TextAdaptive',
    default_technique: 'default',
    default_techniques: defaultMembers,
    default_datasets: defaultDatasets,
    dataset_size_limit: {
      default_scope: 'per_dataset',
      default_count: 4,
      override_scope: 'per_dataset',
    },
    aggregate_techniques: ['all', 'default', 'core', 'extra', 'light', 'multi_turn', 'single_turn'],
    aggregate_technique_expansions: aggregateTechniqueExpansions,
    all_techniques: [...new Set(Object.values(aggregateTechniqueExpansions).flat())],
    supported_parameters: [
      {
        name: 'max_attempts_per_objective',
        type_name: 'int',
        required: false,
        default: 3,
        choices: null,
        is_list: false,
      },
    ],
  })
}

function makeAdaptiveEstimateForRequest(
  scenario: RegisteredScenario,
  request: ScenarioRunSizeEstimateRequest,
): ScenarioDefaultRunSizeEstimate {
  const selectedSet = request.techniques?.[0] ?? 'default'
  const selectedCandidateCount = scenario.aggregate_technique_expansions[selectedSet]?.length ?? 0
  const candidateCount = selectedSet === 'core' ? 5 : selectedCandidateCount
  const configuredMax = Number(request.scenario_params?.max_attempts_per_objective ?? 3)
  const perObjective = Math.min(candidateCount, configuredMax)
  const includeBaseline = request.include_baseline !== false
  return {
    ...makeEstimate(null),
    minimum_attack_count: includeBaseline ? 21 : null,
    maximum_attack_count: includeBaseline ? 42 : 21,
    components: [
      ...(includeBaseline ? [{
        label: 'Baseline',
        count: 21,
        factors: [{ label: 'objectives', count: 21 }],
        is_baseline: true,
        condition: null,
        note: null,
      }] : []),
      {
        label: 'Adaptive objectives',
        count: 21,
        factors: [{ label: 'compatible objectives', count: 21 }],
        is_baseline: false,
        condition: null,
        note: null,
      },
    ],
    adaptive_details: {
      objective_count: 21,
      selected_candidate_technique_count: selectedCandidateCount,
      candidate_technique_count: candidateCount,
      max_attempts_per_objective: configuredMax,
      techniques_per_objective_upper_bound: perObjective,
      technique_attempt_count_upper_bound: 21 * perObjective,
      stop_on_first_success: true,
      compatibility_may_reduce_attempts: true,
    },
  }
}

function makeFullyCompatibleAdaptiveEstimateForRequest(
  scenario: RegisteredScenario,
  request: ScenarioRunSizeEstimateRequest,
): ScenarioDefaultRunSizeEstimate {
  const estimate = makeAdaptiveEstimateForRequest(scenario, request)
  const adaptiveDetails = estimate.adaptive_details
  if (!adaptiveDetails) {
    throw new Error('Expected Adaptive estimate details.')
  }
  const candidateCount = adaptiveDetails.selected_candidate_technique_count ?? 0
  const configuredMaximum = adaptiveDetails.max_attempts_per_objective
  adaptiveDetails.candidate_technique_count = candidateCount
  adaptiveDetails.techniques_per_objective_upper_bound = Math.min(candidateCount, configuredMaximum)
  adaptiveDetails.technique_attempt_count_upper_bound =
    adaptiveDetails.objective_count * adaptiveDetails.techniques_per_objective_upper_bound
  return estimate
}

async function flushRenderedPromises(): Promise<void> {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

async function advanceTimers(milliseconds: number): Promise<void> {
  await act(async () => {
    jest.advanceTimersByTime(milliseconds)
    await Promise.resolve()
  })
}

async function openRunPreview(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  await user.click(screen.getByRole('button', { name: 'Launch scan' }))
  const dialog = await screen.findByRole('dialog', { hidden: true }, { timeout: 5_000 })
  expect(within(dialog).getByText('Run preview')).toBeInTheDocument()
  return dialog
}

async function confirmRunPreview(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await openRunPreview(user)
  await user.click(screen.getByTestId('confirm-launch-scenario-btn'))
}

function renderDetail(
  path: string,
  props: Partial<{
    activeTarget: TargetInstance | null
    labels: Record<string, string>
    onNavigate: (view: string) => void
  }> = {},
) {
  const defaultProps = {
    activeTarget: null,
    labels: { operator: 'roakey' },
    onNavigate: jest.fn(),
  }
  const merged = { ...defaultProps, ...props }
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/scenarios/:scenarioName"
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            element={<ScenarioDetail {...(merged as any)} />}
          />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScenarioDetail', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetScenario.mockReset()
    mockEstimateRun.mockReset()
    mockListTargets.mockReset()
    mockListDatasets.mockReset()
    mockStartRun.mockReset()
    mockListTargets.mockResolvedValue({
      items: [makeTarget('target-a'), makeTarget('target-b')],
      pagination: { limit: 200, has_more: false },
    })
    mockListDatasets.mockResolvedValue({
      items: [
        { name: 'harmbench' },
        { name: 'ds_a' },
        { name: 'ds_b' },
        { name: 'xstest' },
      ],
    })
    mockGetScenario.mockResolvedValue(makeScenario())
    mockEstimateRun.mockReturnValue(new Promise(() => {}))
    mockStartRun.mockResolvedValue({ scenario_result_id: 'sr-default' })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('shows a loading state while fetching', () => {
    mockGetScenario.mockReturnValue(new Promise(() => {}))
    mockListTargets.mockReturnValue(new Promise(() => {}))
    renderDetail('/scenarios/foundry.red_team_agent')
    expect(screen.getByText('Loading scenario...')).toBeInTheDocument()
  })

  it('decodes the scenario name from the URL exactly once', async () => {
    renderDetail('/scenarios/foundry.red_team_agent');
    await screen.findByTestId('scenario-target-select')
    expect(mockGetScenario).toHaveBeenCalledWith('foundry.red_team_agent')
  })

  it('decodes a slash-bearing encoded scenario name back to the original', async () => {
    renderDetail('/scenarios/foundry%2Fred_team_agent')
    await waitFor(() => expect(mockGetScenario).toHaveBeenCalledWith('foundry/red_team_agent'))
  })

  it('preserves a literal percent sequence in a scenario registry name', async () => {
    renderDetail('/scenarios/discount%2550')
    await waitFor(() => expect(mockGetScenario).toHaveBeenCalledWith('discount%50'))
  })

  it('handles a malformed percent sequence without throwing during render', async () => {
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    mockGetScenario.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'not found' } },
    })
    renderDetail('/scenarios/%zz')
    expect(await screen.findByTestId('scenario-not-found')).toBeInTheDocument()
    expect(mockGetScenario).toHaveBeenCalledWith('%zz')
    consoleWarn.mockRestore()
  })

  it('shows a distinct not-found state for a 404, with a link back to the catalog', async () => {
    mockGetScenario.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'not found' } },
    })

    renderDetail('/scenarios/missing.scenario')

    expect(await screen.findByTestId('scenario-not-found')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to scenarios/i })).toHaveAttribute('href', '/scenarios')
    expect(screen.queryByTestId('scenario-error')).not.toBeInTheDocument()
  })

  it('shows a generic error state with retry for a non-404 failure', async () => {
    const user = userEvent.setup()
    mockGetScenario
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'boom' } } })
      .mockResolvedValueOnce(makeScenario())

    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByTestId('scenario-error')).toBeInTheDocument()
    expect(screen.getByText('boom')).toBeInTheDocument()
    expect(screen.queryByTestId('scenario-not-found')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('retry-btn'))
    expect(await screen.findByTestId('scenario-target-select')).toBeInTheDocument()
  })

  it('estimates without a target and directs to Targets before launch', async () => {
    jest.useFakeTimers()
    const onNavigate = jest.fn()
    mockListTargets.mockResolvedValueOnce({ items: [], pagination: { limit: 200, has_more: false } })

    renderDetail('/scenarios/foundry.red_team_agent', { onNavigate })

    expect(screen.getByTestId('scenario-target-select')).toHaveValue('')
    expect(mockEstimateRun).toHaveBeenCalledWith(
      'foundry.red_team_agent',
      {
        techniques: ['default_technique'],
        include_baseline: true,
      },
      expect.any(AbortSignal),
    )
    expect(within(screen.getByTestId('run-estimate')).getByText('8')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Configure target to launch' }))
    expect(onNavigate).toHaveBeenCalledWith('targets')
  })

  it('defaults the target selector to the active target when it is among the fetched targets', async () => {
    renderDetail('/scenarios/foundry.red_team_agent', { activeTarget: makeTarget('target-b') })

    expect(await screen.findByTestId('scenario-target-select')).toHaveValue('target-b')
  })

  it('defaults the target selector to the first fetched target when there is no matching active target', async () => {
    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByTestId('scenario-target-select')).toHaveValue('target-a')
  })

  it('exposes the configuration form and run preview as ordered landmarks', async () => {
    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByRole('form', { name: 'Scenario run configuration' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' })).toBeInTheDocument()
  })

  it('debounces preview requests and aborts the superseded request', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()

    expect(screen.getByTestId('scenario-target-select')).toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()

    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    const firstSignal = mockEstimateRun.mock.calls[0][2] as AbortSignal
    expect(firstSignal.aborted).toBe(false)

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    expect(firstSignal.aborted).toBe(true)
    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-a')
    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')

    await advanceTimers(299)
    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    await advanceTimers(1)
    expect(mockEstimateRun).toHaveBeenCalledTimes(2)
    expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({ target_name: 'target-b' }),
      expect.any(AbortSignal),
    )
  })

  it('ignores an out-of-order estimate response even when the request promise does not abort', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    let resolveFirst: (estimate: ScenarioDefaultRunSizeEstimate) => void = () => {}
    let resolveSecond: (estimate: ScenarioDefaultRunSizeEstimate) => void = () => {}
    mockEstimateRun
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveFirst = resolve
      }))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveSecond = resolve
      }))

    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await advanceTimers(300)

    resolveSecond(makeEstimate(12))
    await flushRenderedPromises()
    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByRole('group', { name: '12 planned attacks.' })).toBeInTheDocument()

    resolveFirst(makeEstimate(8))
    await flushRenderedPromises()
    expect(within(preview).getByRole('group', { name: '12 planned attacks.' })).toBeInTheDocument()
    expect(within(preview).queryByRole('group', { name: '8 planned attacks.' })).not.toBeInTheDocument()
  })

  it('clears prior arithmetic and keeps entered state after a transient preview failure', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockEstimateRun
      .mockResolvedValueOnce(makeEstimate(8))
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: { status: 503, data: { detail: 'Preview service unavailable' } },
      })

    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(screen.getByRole('group', { name: '8 planned attacks.' })).toBeInTheDocument()

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await advanceTimers(300)
    await flushRenderedPromises()

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('target-b')).toBeInTheDocument()
    expect(within(preview).getByText('Run size couldn’t be updated.')).toBeInTheDocument()
    expect(within(preview).queryByRole('group', { name: '8 planned attacks.' })).not.toBeInTheDocument()
    expect(within(preview).getByText('Preview service unavailable')).toBeInTheDocument()
    expect(screen.getByTestId('scenario-target-select')).toHaveValue('target-b')
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
  })

  it('hides stale arithmetic and blocks launch after a configuration request error', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockEstimateRun
      .mockResolvedValueOnce(makeEstimate(8))
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: {
          status: 400,
          data: {
            detail: "Scenario 'adaptive.text_adaptive' does not support overriding dataset names.",
          },
        },
      })
    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(screen.getByRole('group', { name: '8 planned attacks.' })).toBeInTheDocument()

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await advanceTimers(300)
    await flushRenderedPromises()

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('Run size couldn’t be updated.')).toBeInTheDocument()
    expect(within(preview).queryByTestId('run-calculation')).not.toBeInTheDocument()
    expect(within(preview).getByText(
      "Scenario 'adaptive.text_adaptive' does not support overriding dataset names.",
    )).toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
  })

  it('does not request a preview while the custom technique selection is empty', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()

    await user.click(screen.getByTestId('technique-mode-custom'))
    await user.click(screen.getByTestId('technique-crescendo'))
    await advanceTimers(300)

    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(screen.getByText('Complete the required configuration to request an estimate.'))
      .toBeInTheDocument()
  })

  it('renders an unknown conditional estimate without inventing a total', async () => {
    jest.useFakeTimers()
    mockEstimateRun.mockResolvedValue(makeEstimate(null))
    renderDetail('/scenarios/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('Run size is confirmed at launch.')).toBeInTheDocument()
    expect(within(preview).queryByText(REMOVED_NORMAL_ESTIMATE_LABELS)).not.toBeInTheDocument()
    expect(within(preview).queryByText(/planned attacks/)).not.toBeInTheDocument()
  })

  it('renders MyST literals through the shared safe Markdown renderer', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        scenario_type: 'Jailbreak',
        scenario_version: 4,
        description: 'Configure this scenario.',
        description_markdown: `Set \`\`num_jailbreaks\`\`.\n\n${RAW_IMAGE_HTML}unsafe`,
      }),
    )
    renderDetail('/scenarios/foundry.red_team_agent')

    const description = await screen.findByTestId('scenario-detail-description')
    expect(screen.getByText('Jailbreak · v4')).toBeInTheDocument()
    expect(within(description).getByText('num_jailbreaks').tagName).toBe('CODE')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(
      within(description).getByText((content: string) => content.includes(`${RAW_IMAGE_HTML}unsafe`)),
    ).toBeInTheDocument()
  })

  it('initializes the technique selection from default_technique', async () => {
    renderDetail('/scenarios/foundry.red_team_agent')

    await screen.findByTestId('scenario-target-select')
    expect(screen.getByTestId('technique-default_technique')).toBeChecked()
    expect(screen.queryByRole('group', { name: 'Individual techniques' })).not.toBeInTheDocument()
    expect(screen.getByTestId('selected-technique-set-members')).toHaveTextContent('crescendo')
  })

  it('marks a named technique set as the scenario default', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        default_technique: 'easy',
        default_techniques: ['crescendo'],
        aggregate_techniques: ['easy'],
        aggregate_technique_expansions: { easy: ['crescendo'] },
      }),
    )

    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByLabelText('Easy (default) — 1 technique')).toBeChecked()
  })

  it('shows catalog-provided aggregate members before the configured estimate resolves', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        default_technique: 'default',
        default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
        aggregate_techniques: ['default'],
        aggregate_technique_expansions: {
          default: ['prompt_sending', 'jailbreak_system_prompt'],
        },
        all_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
      }),
    )

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText(
      'Resolves to prompt_sending, jailbreak_system_prompt',
    )).toBeInTheDocument()
    expect(within(preview).getByText('Calculating planned attacks...')).toBeInTheDocument()
  })

  it('renders the initial Adaptive conditional estimate instead of an unavailable exact total', async () => {
    mockGetScenario.mockResolvedValue(makeAdaptiveScenario())
    mockEstimateRun.mockResolvedValue({
      version: 1,
      status: 'conditional',
      total_attack_count: null,
      minimum_attack_count: 21,
      maximum_attack_count: 42,
      condition: null,
      components: [
        {
          label: 'Baseline',
          count: 21,
          factors: [{ label: 'selected logical seed groups', count: 21 }],
          is_baseline: true,
          condition: null,
          note: null,
        },
        {
          label: 'Adaptive attack envelopes',
          count: 21,
          factors: [{ label: 'compatible logical seed groups', count: 21 }],
          is_baseline: false,
          condition: null,
          note: null,
        },
      ],
      datasets: [],
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
      note: 'Compatibility and early success may reduce the underlying attempt count.',
      retries_included: false,
    } satisfies ScenarioDefaultRunSizeEstimate)

    renderDetail('/scenarios/adaptive.text_adaptive')

    for (const datasetName of makeAdaptiveScenario().default_datasets) {
      expect(await screen.findByTestId(`dataset-${datasetName}`)).toBeChecked()
    }
    expect(screen.getByText('7 datasets selected')).toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledWith(
      'adaptive.text_adaptive',
      {
        target_name: 'target-a',
        techniques: ['default'],
        include_baseline: true,
      },
      expect.any(AbortSignal),
    ))
    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()
    expect(screen.queryByText('Exact total unavailable')).not.toBeInTheDocument()
  })

  it('updates Adaptive estimates for subset, restored, single, and failed dataset requests', async () => {
    jest.useFakeTimers()
    const scenario = makeAdaptiveScenario()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const objectiveCounts = new Map([
      ['airt_hate', 4],
      ['airt_fairness', 1],
      ['airt_violence', 3],
      ['airt_sexual', 3],
      ['airt_harassment', 3],
      ['airt_misinformation', 3],
      ['airt_leakage', 4],
    ])
    let failNextRequest = false
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(async (_scenarioName, request: ScenarioRunSizeEstimateRequest) => {
      if (failNextRequest) {
        failNextRequest = false
        throw {
          isAxiosError: true,
          response: { status: 503, data: { detail: 'Current estimate failed' } },
        }
      }
      const datasetNames = request.dataset_names ?? scenario.default_datasets
      const objectiveCount = datasetNames.reduce(
        (count, datasetName) => count + (objectiveCounts.get(datasetName) ?? 0),
        0,
      )
      const configuredMaximum = Number(request.scenario_params?.max_attempts_per_objective ?? 3)
      const effectiveMaximum = Math.min(2, configuredMaximum)
      return {
        ...makeEstimate(null),
        minimum_attack_count: objectiveCount,
        maximum_attack_count: objectiveCount * 2,
        components: [
          {
            label: 'Baseline',
            count: objectiveCount,
            factors: [{ label: 'objectives', count: objectiveCount }],
            is_baseline: true,
            condition: null,
            note: null,
          },
          {
            label: 'Adaptive objectives',
            count: objectiveCount,
            factors: [{ label: 'objectives', count: objectiveCount }],
            is_baseline: false,
            condition: null,
            note: null,
          },
        ],
        adaptive_details: {
          objective_count: objectiveCount,
          selected_candidate_technique_count: 2,
          candidate_technique_count: 2,
          max_attempts_per_objective: configuredMaximum,
          techniques_per_objective_upper_bound: effectiveMaximum,
          technique_attempt_count_upper_bound: objectiveCount * effectiveMaximum,
          stop_on_first_success: true,
          compatibility_may_reduce_attempts: true,
        },
      }
    })

    renderDetail('/scenarios/adaptive.text_adaptive')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(screen.getByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()

    await user.click(screen.getByTestId('dataset-airt_fairness'))
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(mockEstimateRun.mock.calls.at(-1)?.[1].dataset_names).toEqual([
      'airt_hate',
      'airt_violence',
      'airt_sexual',
      'airt_harassment',
      'airt_misinformation',
      'airt_leakage',
    ])
    expect(screen.getByRole('group', {
      name: '20 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 40 technique attempts.',
    })).toBeInTheDocument()

    await user.click(screen.getByTestId('restore-default-datasets'))
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('dataset_names')
    expect(screen.getByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()

    for (const datasetName of scenario.default_datasets.filter((name) => name !== 'airt_fairness')) {
      await user.click(screen.getByTestId(`dataset-${datasetName}`))
    }
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(mockEstimateRun.mock.calls.at(-1)?.[1].dataset_names).toEqual(['airt_fairness'])
    expect(screen.getByRole('group', {
      name: '1 objective multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 2 technique attempts.',
    })).toBeInTheDocument()

    failNextRequest = true
    await user.click(screen.getByTestId('dataset-airt_hate'))
    await advanceTimers(300)
    await flushRenderedPromises()
    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).queryByTestId('run-calculation')).not.toBeInTheDocument()
    expect(within(preview).getByText('Current estimate failed')).toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
  })

  it('explains Adaptive technique sets, progress objectives, and bounded attempt work', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => makeAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()

    renderDetail('/scenarios/adaptive.text_adaptive')

    expect(await screen.findByLabelText('Recommended (default) — 2 techniques')).toBeChecked()
    expect(screen.getByLabelText('All (17 techniques)')).not.toBeChecked()
    expect(screen.getByLabelText('Core (14 techniques)')).toBeInTheDocument()
    expect(screen.getByLabelText('Extra (3 techniques)')).toBeInTheDocument()
    expect(screen.getByLabelText('Light (9 techniques)')).toBeInTheDocument()
    expect(screen.getByLabelText('Multi-turn (5 techniques)')).toBeInTheDocument()
    expect(screen.getByLabelText('Single-turn (12 techniques)')).toBeInTheDocument()
    expect(screen.getByText(
      'Choose a predefined set, or choose Custom to select techniques individually.',
    )).toBeInTheDocument()
    expect(screen.getByText(
      /All is generated from the catalog; Recommended is curated for this scenario/,
    )).toBeInTheDocument()
    expect(screen.getByText(
      /tries no more than the configured maximum or the compatible candidate count, whichever is smaller/,
    )).toBeInTheDocument()
    expect(screen.getByText(
      /compatibility can still change how many objectives can run/,
    )).toBeInTheDocument()
    expect(screen.queryByText(/aggregate preset/i)).not.toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Custom' })).not.toBeChecked()
    expect(screen.queryByRole('group', { name: 'Individual techniques' })).not.toBeInTheDocument()

    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()

    await user.click(screen.getByLabelText('Core (14 techniques)'))
    expect(screen.getByLabelText('Core (14 techniques)')).toBeChecked()
    const selectedMembers = screen.getByTestId('selected-technique-set-members')
    expect(within(selectedMembers).getByText('core_member_14')).toBeInTheDocument()
    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 5 compatible candidates from 14 selected and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()
    expect(screen.getAllByText(
      /5 compatible candidates from 14 selected · limit 2/,
    )).toHaveLength(2)

    const maxAttempts = screen.getByRole('spinbutton', { name: 'Maximum techniques per objective' })
    expect(screen.getByText(
      /This is a per-objective limit, not a total-run budget/,
    )).toBeInTheDocument()
    expect(screen.getByText(/incompatible techniques are skipped/)).toBeInTheDocument()
    expect(screen.getByText(/This is separate from retries/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByText(
      'Maximum times to resume the scenario after an exception. This is separate from Adaptive trying another technique.',
    )).toBeInTheDocument()
    await user.clear(maxAttempts)
    await user.type(maxAttempts, '1')
    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 1 technique per objective, the smaller of 5 compatible candidates from 14 selected and limit 1, equals up to 21 technique attempts.',
    })).toBeInTheDocument()
    expect(screen.getAllByText(
      /5 compatible candidates from 14 selected · limit 1/,
    )).toHaveLength(2)

    await user.clear(maxAttempts)
    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 3 techniques per objective, the smaller of 5 compatible candidates from 14 selected and limit 3, equals up to 63 technique attempts.',
    })).toBeInTheDocument()

    await user.type(maxAttempts, '0')
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
    expect(screen.queryByText(/up to 0 technique/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/objective envelope/i)).not.toBeInTheDocument()
  })

  it('validates the Adaptive attempt limit locally and recomputes after correction', async () => {
    jest.useFakeTimers()
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => makeAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })

    renderDetail('/scenarios/adaptive.text_adaptive')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()

    const maxAttempts = screen.getByRole('spinbutton', { name: 'Maximum techniques per objective' })
    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(maxAttempts).toHaveAttribute('min', '1')
    expect(maxAttempts).toHaveAttribute('step', '1')
    expect(maxAttempts).toHaveAttribute('inputmode', 'numeric')
    expect(maxAttempts).toHaveAttribute('pattern', '[0-9]*')
    expect(screen.getByText(
      /Blank restores the bounded default of 2 techniques per objective for this target\./,
    )).toBeInTheDocument()
    expect(screen.queryByText(/Leave blank to use the default of 3/)).not.toBeInTheDocument()
    expect(maxAttempts).toHaveValue(2)
    expect(within(preview).getByText('up to 42')).toBeInTheDocument()
    const initialRequestCount = mockEstimateRun.mock.calls.length

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '-8')
    expect(maxAttempts).toHaveValue(null)
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
    expect(within(preview).getByText(CORRECT_HIGHLIGHTED_SETTING_MESSAGE))
      .toBeInTheDocument()
    expect(within(preview).queryByTestId('run-calculation')).not.toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(initialRequestCount)

    await user.tab()
    await user.click(maxAttempts)
    await user.paste('-8')
    expect(maxAttempts).toHaveValue(null)
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(initialRequestCount)

    await user.tab()
    await user.click(maxAttempts)
    await user.paste('0')
    expect(maxAttempts).toHaveValue(null)
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(initialRequestCount)

    for (const invalidValue of ['1.5', '1e3', '+8']) {
      await user.tab()
      await user.click(maxAttempts)
      await user.type(maxAttempts, invalidValue)
      expect(maxAttempts).toHaveValue(null)
      expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
      expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
      expect(within(preview).getByText(CORRECT_HIGHLIGHTED_SETTING_MESSAGE))
        .toBeInTheDocument()
      expect(within(preview).queryByTestId('run-calculation')).not.toBeInTheDocument()
      expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
      expect(screen.queryByText(/max_attempts_per_objective must/i)).not.toBeInTheDocument()
      await advanceTimers(300)
      expect(mockEstimateRun).toHaveBeenCalledTimes(initialRequestCount)
    }

    await user.tab()
    await user.click(maxAttempts)
    await user.type(maxAttempts, '0')
    expect(maxAttempts).toHaveValue(null)
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(initialRequestCount)

    await user.tab()
    await user.click(maxAttempts)
    await user.type(maxAttempts, '1')
    await user.clear(maxAttempts)
    await advanceTimers(300)
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'false')
    expect(maxAttempts).toHaveValue(2)
    expect(mockEstimateRun.mock.calls.at(-1)?.[1].scenario_params).toEqual({
      max_attempts_per_objective: 2,
    })
    expect(within(preview).getByText('up to 42')).toBeInTheDocument()

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '1')
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'false')
    expect(mockEstimateRun.mock.calls.at(-1)?.[1].scenario_params).toEqual({
      max_attempts_per_objective: 1,
    })
    expect(within(within(preview).getByTestId('adaptive-work-calculation')).getByText('up to 21'))
      .toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).toBeEnabled()

    const correctedRequestCount = mockEstimateRun.mock.calls.length
    Object.defineProperty(window.getSelection(), 'modify', { value: jest.fn(), configurable: true })
    await user.keyboard('{ArrowDown}')
    expect(maxAttempts).toHaveValue(1)
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledTimes(correctedRequestCount)
  })

  it('does not let a superseded estimate repopulate arithmetic after the limit becomes invalid', async () => {
    jest.useFakeTimers()
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    let resolveEstimate: (estimate: ScenarioDefaultRunSizeEstimate) => void = () => {}
    mockEstimateRun
      .mockResolvedValueOnce(makeAdaptiveEstimateForRequest(scenario, {
        target_name: 'target-a',
        techniques: ['default'],
        include_baseline: true,
      }))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveEstimate = resolve
      }))
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })

    renderDetail('/scenarios/adaptive.text_adaptive')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    const maxAttempts = screen.getByRole('spinbutton', { name: 'Maximum techniques per objective' })
    await user.type(maxAttempts, '1')
    await advanceTimers(300)
    const requestSignal = mockEstimateRun.mock.calls[1][2] as AbortSignal

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '-8')
    expect(requestSignal.aborted).toBe(true)
    expect(screen.getByText(CORRECT_HIGHLIGHTED_SETTING_MESSAGE)).toBeInTheDocument()

    resolveEstimate(makeAdaptiveEstimateForRequest(scenario, {
      target_name: 'target-a',
      techniques: ['default'],
      include_baseline: true,
      scenario_params: { max_attempts_per_objective: 3 },
    }))
    await flushRenderedPromises()

    expect(screen.queryByTestId('run-calculation')).not.toBeInTheDocument()
    expect(screen.getByText(CORRECT_HIGHLIGHTED_SETTING_MESSAGE)).toBeInTheDocument()
    expect(mockEstimateRun).toHaveBeenCalledTimes(2)
  })

  it('maps backend attempt-limit validation to the field without exposing internal copy', async () => {
    jest.useFakeTimers()
    mockGetScenario.mockResolvedValue(makeAdaptiveScenario())
    mockEstimateRun.mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 400,
        data: { detail: 'max_attempts_per_objective must be >= 1, got -8' },
      },
    })

    renderDetail('/scenarios/adaptive.text_adaptive')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()

    const maxAttempts = screen.getByRole('spinbutton', { name: 'Maximum techniques per objective' })
    expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
    expect(screen.getByText(CORRECT_HIGHLIGHTED_SETTING_MESSAGE)).toBeInTheDocument()
    expect(screen.queryByText(/max_attempts_per_objective must/i)).not.toBeInTheDocument()
    expect(screen.queryByTestId('run-calculation')).not.toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
  })

  it('presents Adaptive attempts clearly while preserving the scenario parameter wire key', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => makeAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')

    const maxAttempts = await screen.findByRole('spinbutton', { name: 'Maximum techniques per objective' })
    await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })
    expect(maxAttempts).toHaveAttribute('max', '2')
    expect(maxAttempts).toHaveValue(2)
    expect(screen.getByText(
      'The scenario default of 3 is reduced to 2 because Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()
    expect(screen.queryByText(/Maximum reached:/)).not.toBeInTheDocument()
    expect(screen.getByText(
      /Blank restores the bounded default of 2 techniques per objective for this target\./,
    )).toBeInTheDocument()
    expect(screen.queryByText('max_attempts_per_objective')).not.toBeInTheDocument()
    expect(screen.getByText(/This is separate from retries/)).toBeInTheDocument()

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '5')
    expect(maxAttempts).toHaveValue(2)
    expect(screen.getByText(
      'Maximum reached: Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        scenario_params: { max_attempts_per_objective: 5 },
      }),
      expect.any(AbortSignal),
    )

    await user.clear(maxAttempts)
    await waitFor(() => expect(maxAttempts).toHaveValue(2))
    expect(screen.getByText(
      'The scenario default of 3 is reduced to 2 because Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()

    await user.paste('3')
    expect(maxAttempts).toHaveValue(2)
    expect(screen.getByText(
      'Maximum reached: Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '1')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        scenario_params: { max_attempts_per_objective: 1 },
      }),
      expect.any(AbortSignal),
    ))
    expect(screen.queryByText(/Maximum reached:/)).not.toBeInTheDocument()

    await user.clear(maxAttempts)
    await user.type(maxAttempts, '2')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        scenario_params: { max_attempts_per_objective: 2 },
      }),
      expect.any(AbortSignal),
    ))
    expect(await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })).toBeInTheDocument()
    expect(screen.getByText(
      'Maximum reached: Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()
    Object.defineProperty(window.getSelection(), 'modify', { value: jest.fn(), configurable: true })
    await user.keyboard('{ArrowUp}')
    expect(maxAttempts).toHaveValue(2)
    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('Maximum techniques per objective')).toBeInTheDocument()
    expect(within(preview).queryByText('Techniques tried per objective')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalledWith(
      expect.objectContaining({
        scenario_params: { max_attempts_per_objective: 2 },
      }),
    ))
  })

  it.each([
    ['Light (9 techniques)', 'Light', 9, 10],
    ['Core (14 techniques)', 'Core', 14, 22],
    ['All (17 techniques)', 'All', 17, 22],
  ])(
    'clamps an over-limit attempt to the authoritative %s candidate count',
    async (optionLabel, displayName, maximum, attemptedValue) => {
      const scenario = makeAdaptiveScenario()
      mockGetScenario.mockResolvedValue(scenario)
      mockEstimateRun.mockImplementation(
        async (
          _scenarioName: string,
          request: ScenarioRunSizeEstimateRequest,
        ): Promise<ScenarioDefaultRunSizeEstimate> => {
          const estimate = makeAdaptiveEstimateForRequest(scenario, request)
          const adaptiveDetails = estimate.adaptive_details
          if (adaptiveDetails) {
            const candidateCount = adaptiveDetails.selected_candidate_technique_count ?? 0
            const configuredMaximum = adaptiveDetails.max_attempts_per_objective
            adaptiveDetails.candidate_technique_count = candidateCount
            adaptiveDetails.techniques_per_objective_upper_bound = Math.min(
              candidateCount,
              configuredMaximum,
            )
            adaptiveDetails.technique_attempt_count_upper_bound =
              21 * adaptiveDetails.techniques_per_objective_upper_bound
          }
          return estimate
        },
      )
      const user = userEvent.setup()
      renderDetail('/scenarios/adaptive.text_adaptive')

      const maxAttempts = await screen.findByRole('spinbutton', {
        name: 'Maximum techniques per objective',
      })
      await user.click(await screen.findByLabelText(optionLabel))
      await waitFor(() => expect(maxAttempts).toHaveAttribute('max', String(maximum)))
      expect(screen.getByText(/Leave blank to use the default of 3\./)).toBeInTheDocument()
      expect(screen.queryByText(/Blank restores the bounded default/)).not.toBeInTheDocument()
      await user.clear(maxAttempts)
      await user.type(maxAttempts, String(attemptedValue))

      expect(maxAttempts).toHaveValue(maximum)
      expect(screen.getByText(
        `Maximum reached: ${displayName} provides ${maximum} compatible techniques for this target.`,
      )).toBeInTheDocument()
      expect(await screen.findByRole('group', {
        name: `21 objectives multiplied by up to ${maximum} techniques per objective, the smaller of ${maximum} selected candidates and limit ${maximum}, equals up to ${
          21 * maximum
        } technique attempts.`,
      })).toBeInTheDocument()
      expect(mockEstimateRun).not.toHaveBeenCalledWith(
        'adaptive.text_adaptive',
        expect.objectContaining({
          scenario_params: { max_attempts_per_objective: attemptedValue },
        }),
        expect.any(AbortSignal),
      )
      expect(mockEstimateRun).toHaveBeenLastCalledWith(
        'adaptive.text_adaptive',
        expect.objectContaining({
          scenario_params: { max_attempts_per_objective: maximum },
        }),
        expect.any(AbortSignal),
      )
      expect(screen.getByRole('complementary', { name: 'Run preview' })).toHaveTextContent(
        new RegExp(`Maximum techniques per objective\\s*${maximum}`),
      )
      await user.type(maxAttempts, '-8')
      expect(maxAttempts).toHaveValue(maximum)
      expect(maxAttempts).toHaveAttribute('aria-invalid', 'true')
      expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    },
  )

  it('clamps an explicit limit when the selected technique set lowers the compatible maximum', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => makeAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')

    const maxAttempts = await screen.findByRole('spinbutton', {
      name: 'Maximum techniques per objective',
    })
    await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })

    await user.click(screen.getByLabelText('Core (14 techniques)'))
    await waitFor(() => expect(maxAttempts).toHaveAttribute('max', '5'))
    await user.type(maxAttempts, '5')
    await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 5 techniques per objective, the smaller of 5 compatible candidates from 14 selected and limit 5, equals up to 105 technique attempts.',
    })

    await user.click(screen.getByLabelText('Recommended (default) — 2 techniques'))
    expect(maxAttempts).toBeDisabled()
    await waitFor(() => expect(maxAttempts).toHaveValue(2))
    expect(maxAttempts).toHaveAttribute('max', '2')
    expect(screen.getByText(
      'Reduced to 2 because Recommended (default) provides 2 compatible techniques for this target.',
    )).toBeInTheDocument()
    await screen.findByRole('group', {
      name: '21 objectives multiplied by up to 2 techniques per objective, the smaller of 2 selected candidates and limit 2, equals up to 42 technique attempts.',
    })
    expect(mockEstimateRun).not.toHaveBeenCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        techniques: ['default'],
        scenario_params: { max_attempts_per_objective: 5 },
      }),
      expect.any(AbortSignal),
    )

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalledWith(
      expect.objectContaining({
        techniques: ['default'],
        scenario_params: { max_attempts_per_objective: 2 },
      }),
    ))
  })

  it('updates the bound for target compatibility and blocks a target with no eligible techniques', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => {
        const estimate = makeAdaptiveEstimateForRequest(scenario, request)
        if (request.target_name === 'target-b' && estimate.adaptive_details) {
          const candidateCount = request.techniques?.[0] === 'core' ? 0 : 1
          estimate.adaptive_details.candidate_technique_count = candidateCount
          estimate.adaptive_details.techniques_per_objective_upper_bound = candidateCount
          estimate.adaptive_details.technique_attempt_count_upper_bound = 21 * candidateCount
        }
        return estimate
      },
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')

    const maxAttempts = await screen.findByRole('spinbutton', {
      name: 'Maximum techniques per objective',
    })
    await waitFor(() => expect(maxAttempts).toHaveAttribute('max', '2'))
    await user.type(maxAttempts, '2')
    await waitFor(() => expect(maxAttempts).toHaveValue(2))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), 'target-b')

    expect(maxAttempts).toBeDisabled()
    await waitFor(() => expect(maxAttempts).toHaveValue(1))
    expect(maxAttempts).toHaveAttribute('max', '1')
    expect(screen.getByText(
      'Reduced to 1 because Recommended (default) provides 1 compatible technique for this target.',
    )).toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        target_name: 'target-b',
        scenario_params: { max_attempts_per_objective: 2 },
      }),
      expect.any(AbortSignal),
    )

    await user.click(screen.getByLabelText('Core (14 techniques)'))
    expect(await screen.findByText(
      'No compatible techniques are available for this target. Choose a different technique set or target.',
    )).toBeInTheDocument()
    expect(maxAttempts).toBeDisabled()
    expect(maxAttempts).not.toHaveAttribute('max')
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(screen.queryByTestId('run-calculation')).not.toBeInTheDocument()
  })

  it('switches exclusively from a named set to Custom and initializes resolved members', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        aggregate_techniques: ['default_technique', 'all_garak'],
        all_techniques: ['default_technique', 'crescendo', 'prompt_sending', 'all_garak'],
      }),
    )
    const user = userEvent.setup()

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    // 'all_garak' is both an aggregate and (accidentally) listed under all_techniques —
    // it must render exactly once (deduped), under the aggregate group.
    expect(screen.getAllByTestId('technique-all_garak')).toHaveLength(1)

    expect(screen.queryByRole('group', { name: 'Individual techniques' })).not.toBeInTheDocument()
    await user.click(screen.getByTestId('technique-mode-custom'))
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()

    await user.click(screen.getByTestId('technique-prompt_sending'))
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({ techniques: ['crescendo', 'prompt_sending'] }),
      expect.any(AbortSignal),
    ))
    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request.techniques).toEqual(['crescendo', 'prompt_sending'])
    expect(new Set(request.techniques).size).toBe(request.techniques.length)
  })

  it('preserves custom choices while named sets send exactly one token', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        aggregate_techniques: ['default_technique', 'all_garak'],
        aggregate_technique_expansions: {
          default_technique: ['crescendo'],
          all_garak: ['crescendo'],
        },
        all_techniques: ['default_technique', 'crescendo', 'prompt_sending'],
      }),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('technique-mode-custom'))
    await user.click(screen.getByTestId('technique-prompt_sending'))
    await user.click(screen.getByTestId('technique-all_garak'))
    expect(screen.getByTestId('technique-all_garak')).toBeChecked()
    expect(screen.queryByRole('group', { name: 'Individual techniques' })).not.toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({ techniques: ['all_garak'] }),
      expect.any(AbortSignal),
    ))

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].techniques).toEqual(['all_garak'])

    await user.click(screen.getByTestId('technique-mode-custom'))
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()
    expect(screen.getByTestId('technique-prompt_sending')).toBeChecked()
  })

  it('initializes a concrete default as custom and allows adding another concrete technique', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        default_technique: 'prompt_sending',
        aggregate_techniques: ['all_garak'],
        all_techniques: ['prompt_sending', 'crescendo'],
      }),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByTestId('technique-mode-custom')).toBeChecked()
    expect(screen.getByTestId('technique-prompt_sending')).toBeChecked()
    await user.click(screen.getByTestId('technique-crescendo'))
    expect(screen.getByTestId('technique-prompt_sending')).toBeChecked()
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].techniques).toEqual(['prompt_sending', 'crescendo'])
  })

  it('keeps an explicit invalid custom state when the last concrete technique is removed', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('technique-mode-custom'))
    await user.click(screen.getByTestId('technique-crescendo'))

    expect(await screen.findByRole('alert')).toHaveTextContent('Select at least one technique.')
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('defaults the baseline checkbox from include_baseline_by_default when enabled, and allows editing', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const checkbox = screen.getByTestId('baseline-checkbox')
    expect(checkbox).toBeChecked()
    expect(checkbox).toHaveAccessibleName('baseline')
    expect(screen.getByText(
      /Also send each selected objective directly, without an attack technique/,
    )).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' })).toHaveTextContent(
      'Included — direct objective without an attack technique',
    )

    await user.click(checkbox)
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({ include_baseline: false }),
      expect.any(AbortSignal),
    ))
    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].include_baseline).toBe(false)
  })

  it('updates Adaptive planned arithmetic ON to OFF to ON while preserving inner work', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> =>
        makeFullyCompatibleAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')

    await user.click(await screen.findByLabelText('Core (14 techniques)'))
    const maxAttempts = await screen.findByRole('spinbutton', {
      name: 'Maximum techniques per objective',
    })
    await waitFor(() => expect(maxAttempts).toHaveAttribute('max', '14'))
    await user.clear(maxAttempts)
    await user.type(maxAttempts, '14')

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(await within(preview).findByRole('group', {
      name: 'Direct baseline comparison is included: 21 direct baseline attacks plus up to 21 Adaptive attacks equals 21–42 planned attacks.',
    })).toBeInTheDocument()
    expect(within(preview).getByTestId('adaptive-work-calculation')).toHaveTextContent(
      'up to 294technique attempts',
    )
    expect(screen.getByText('Adds 21 direct baseline attacks for the current objectives.'))
      .toBeInTheDocument()
    expect(within(preview).getByText('Included — direct objective without an attack technique'))
      .toBeInTheDocument()

    const baselineCheckbox = screen.getByTestId('baseline-checkbox')
    await user.click(baselineCheckbox)
    expect(within(preview).getByText('Calculating planned attacks...')).toBeInTheDocument()
    expect(within(preview).getByText('Not included')).toBeInTheDocument()
    expect(await within(preview).findByRole('group', {
      name: 'Direct baseline comparison is not included: up to 21 Adaptive attacks equals up to 21 planned attacks.',
    })).toBeInTheDocument()
    expect(within(preview).getByTestId('adaptive-work-calculation')).toHaveTextContent(
      'up to 294technique attempts',
    )
    expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        include_baseline: false,
        scenario_params: { max_attempts_per_objective: 14 },
      }),
      expect.any(AbortSignal),
    )

    await user.click(baselineCheckbox)
    expect(within(preview).getByText('Calculating planned attacks...')).toBeInTheDocument()
    expect(within(preview).getByText('Included — direct objective without an attack technique'))
      .toBeInTheDocument()
    expect(await within(preview).findByRole('group', {
      name: 'Direct baseline comparison is included: 21 direct baseline attacks plus up to 21 Adaptive attacks equals 21–42 planned attacks.',
    })).toBeInTheDocument()
    expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'adaptive.text_adaptive',
      expect.objectContaining({
        include_baseline: true,
        scenario_params: { max_attempts_per_objective: 14 },
      }),
      expect.any(AbortSignal),
    )
  })

  it('ignores stale Adaptive baseline estimates after a rapid OFF to ON toggle', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> =>
        makeFullyCompatibleAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')

    await screen.findByRole('group', {
      name: 'Direct baseline comparison is included: 21 direct baseline attacks plus up to 21 Adaptive attacks equals 21–42 planned attacks.',
    })

    let resolveOff: ((estimate: ScenarioDefaultRunSizeEstimate) => void) | null = null
    let resolveOn: ((estimate: ScenarioDefaultRunSizeEstimate) => void) | null = null
    mockEstimateRun.mockImplementation(
      async (
        _scenarioName: string,
        request: ScenarioRunSizeEstimateRequest,
      ): Promise<ScenarioDefaultRunSizeEstimate> => await new Promise((resolve) => {
        if (request.include_baseline === false) {
          resolveOff = resolve
        } else {
          resolveOn = resolve
        }
      }),
    )

    const baselineCheckbox = screen.getByTestId('baseline-checkbox')
    await user.click(baselineCheckbox)
    await waitFor(() => expect(resolveOff).not.toBeNull())
    await user.click(baselineCheckbox)
    await waitFor(() => expect(resolveOn).not.toBeNull())

    if (!resolveOn || !resolveOff) {
      throw new Error('Expected both baseline estimate requests to be pending.')
    }
    resolveOn(makeFullyCompatibleAdaptiveEstimateForRequest(scenario, {
      target_name: 'target-a',
      techniques: ['default'],
      include_baseline: true,
    }))
    await flushRenderedPromises()
    expect(screen.getByRole('group', {
      name: 'Direct baseline comparison is included: 21 direct baseline attacks plus up to 21 Adaptive attacks equals 21–42 planned attacks.',
    })).toBeInTheDocument()

    resolveOff(makeFullyCompatibleAdaptiveEstimateForRequest(scenario, {
      target_name: 'target-a',
      techniques: ['default'],
      include_baseline: false,
    }))
    await flushRenderedPromises()
    expect(screen.getByRole('group', {
      name: 'Direct baseline comparison is included: 21 direct baseline attacks plus up to 21 Adaptive attacks equals 21–42 planned attacks.',
    })).toBeInTheDocument()
    expect(screen.queryByRole('group', {
      name: 'Direct baseline comparison is not included: up to 21 Adaptive attacks equals up to 21 planned attacks.',
    })).not.toBeInTheDocument()
  })

  it('defaults the baseline checkbox to unchecked when the policy is disabled with include_baseline_by_default false', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({ baseline_policy: 'disabled', include_baseline_by_default: false }),
    )
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByTestId('baseline-checkbox')).not.toBeChecked()
  })

  it('disables and forces the baseline checkbox false when the policy is forbidden', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({ baseline_policy: 'forbidden' }))
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    const user = userEvent.setup()

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const checkbox = screen.getByTestId('baseline-checkbox')
    expect(checkbox).toBeDisabled()
    expect(checkbox).not.toBeChecked()
    expect(checkbox).toHaveAccessibleName('Include direct baseline comparison')
    expect(screen.getByText(
      /This scenario does not support sending objectives directly without an attack technique/,
    )).toBeInTheDocument()
    expect(await screen.findByRole('group', { name: '8 planned attacks.' })).toBeInTheDocument()
    expect(screen.queryByText(/direct baseline attack/)).not.toBeInTheDocument()

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].include_baseline).toBe(false)
  })

  it('renders scenario-specific parameters and omits common/opaque parameter names', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        supported_parameters: [
          { name: 'objective_target', type_name: 'any', required: false, default: null, choices: null, is_list: false },
          { name: 'max_concurrency', type_name: 'int', required: false, default: null, choices: null, is_list: false },
          { name: 'technique_converters', type_name: 'any', required: false, default: null, choices: null, is_list: false },
          { name: 'custom_flag', type_name: 'bool', required: false, default: null, choices: null, is_list: false },
          { name: 'iterations', type_name: 'int', required: false, default: '3', choices: null, is_list: false },
        ],
      }),
    )

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.queryByTestId('scenario-param-objective_target')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-param-max_concurrency')).not.toBeInTheDocument()
    expect(screen.queryByTestId('scenario-param-technique_converters')).not.toBeInTheDocument()
    expect(screen.getByTestId('scenario-param-custom_flag')).toBeInTheDocument()
    expect(screen.getByTestId('scenario-param-iterations')).toHaveValue(3)
  })

  it('reports a validation error for an invalid custom parameter and blocks submission', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        supported_parameters: [
          { name: 'iterations', type_name: 'int', required: false, default: null, choices: null, is_list: false },
        ],
      }),
    )

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    // A number-typed HTML input rejects non-numeric characters outright, so a
    // decimal (a valid *number* but not a valid *integer*) exercises the same
    // coercion/validation path a real user could actually trigger.
    fireEvent.change(screen.getByTestId('scenario-param-iterations'), { target: { value: '1.5' } })

    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(screen.getByText('iterations must be an integer.')).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('selects scenario default datasets initially and omits the unchanged override', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByTestId('dataset-harmbench')).toBeChecked()
    expect(screen.getByText('1 dataset selected')).toBeInTheDocument()
    expect(screen.getByTestId('restore-default-datasets')).toBeDisabled()
    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request).not.toHaveProperty('dataset_names')
    expect(request).not.toHaveProperty('max_dataset_size')
    expect(request.max_concurrency).toBe(10)
    expect(request.max_retries).toBe(0)
  })

  it('materializes the adaptive per-dataset default while omitting unchanged and restored overrides', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (_scenarioName, request: ScenarioRunSizeEstimateRequest) =>
        makeFullyCompatibleAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))

    const input = screen.getByTestId('advanced-max_dataset_size')
    expect(input).toHaveValue(4)
    expect(input).toHaveAttribute('min', '1')
    expect(input).toHaveAttribute('step', '1')
    expect(input).toHaveAttribute('inputmode', 'numeric')
    expect(screen.getByText(
      'Scenario default: up to 4 objectives from each selected dataset. Enter another whole number to override it, or leave blank to use the scenario default.',
    )).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('4 per dataset (scenario default)')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalled())
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')

    await user.clear(input)
    expect(input).toHaveValue(null)
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('4 per dataset (scenario default)')
    await user.type(input, '3')
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('3 per dataset (override)')
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1]).toEqual(
      expect.objectContaining({ max_dataset_size: 3 }),
    ))
    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].max_dataset_size).toBe(3)
    mockStartRun.mockClear()

    await user.click(screen.getByTestId('restore-default-dataset-size'))
    expect(input).toHaveValue(4)
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('4 per dataset (scenario default)')
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1])
      .not.toHaveProperty('max_dataset_size'))
    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('max_dataset_size')
  })

  it('keeps the inherited per-dataset default while dataset selections change', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (_scenarioName, request: ScenarioRunSizeEstimateRequest) =>
        makeFullyCompatibleAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')
    await screen.findByTestId('dataset-ds_a')

    await user.click(screen.getByTestId('dataset-airt_hate'))
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1]).toEqual(
      expect.objectContaining({
        dataset_names: expect.arrayContaining(['airt_fairness', 'airt_violence']),
      }),
    ))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1].dataset_names).toHaveLength(6)
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')

    for (const name of ['airt_fairness', 'airt_violence', 'airt_sexual', 'airt_harassment', 'airt_misinformation']) {
      await user.click(screen.getByTestId(`dataset-${name}`))
    }
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1].dataset_names)
      .toEqual(['airt_leakage']))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')

    await user.click(screen.getByTestId('restore-default-datasets'))
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1])
      .not.toHaveProperty('dataset_names'))
    await user.click(screen.getByTestId('dataset-ds_a'))
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1].dataset_names)
      .toEqual([...scenario.default_datasets, 'ds_a']))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].dataset_names).toEqual([...scenario.default_datasets, 'ds_a'])
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('max_dataset_size')
  })

  it('renders accurate combined, no-cap, and heterogeneous dataset limit semantics', async () => {
    const combinedScenario = makeScenario({
      default_datasets: ['harmbench', 'xstest'],
      dataset_size_limit: {
        default_scope: 'combined',
        default_count: 5,
        override_scope: 'combined',
      },
    })
    mockGetScenario.mockResolvedValue(combinedScenario)
    const user = userEvent.setup()
    const view = renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByRole('spinbutton', {
      name: 'Maximum objectives across selected datasets',
    })).toHaveValue(5)
    expect(screen.getByText(/Scenario default: up to 5 objectives across the selected datasets/))
      .toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('5 total (scenario default)')

    view.unmount()
    mockGetScenario.mockResolvedValue(makeScenario())
    const noCapView = renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByText(/No scenario default cap/)).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('No additional objective cap')

    noCapView.unmount()
    mockGetScenario.mockResolvedValue(makeScenario({
      default_datasets: ['harmbench', 'xstest'],
      dataset_size_limit: {
        default_scope: 'heterogeneous',
        default_count: null,
        override_scope: 'per_dataset',
      },
    }))
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByText(/Scenario defaults vary by dataset/)).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('Varies by dataset (scenario default)')
  })

  it('disables dataset-size overrides when the scenario manages its population directly', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({
      dataset_size_limit: {
        default_scope: 'none',
        default_count: null,
        override_scope: 'unsupported',
      },
    }))
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))

    expect(screen.getByRole('spinbutton', { name: 'Maximum objectives' })).toBeDisabled()
    expect(screen.getByText(
      'This scenario manages its objective population directly and does not support a dataset-size override.',
    )).toBeInTheDocument()
    expect(screen.queryByTestId('restore-default-dataset-size')).not.toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('No additional objective cap')
  })

  it('filters datasets and sends the exact changed selection to estimate and launch', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.type(screen.getByRole('textbox', { name: 'Search datasets' }), 'ds_')
    expect(screen.queryByTestId('dataset-harmbench')).not.toBeInTheDocument()
    expect(screen.getByTestId('dataset-ds_a')).toBeInTheDocument()
    await user.click(screen.getByTestId('dataset-ds_a'))
    await user.clear(screen.getByRole('textbox', { name: 'Search datasets' }))
    await user.click(screen.getByTestId('dataset-harmbench'))
    expect(screen.getByText('1 dataset selected')).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Run preview' })).toHaveTextContent('ds_a')
    expect(screen.getByRole('complementary', { name: 'Run preview' })).not.toHaveTextContent('harmbench')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    await user.type(screen.getByTestId('advanced-max_dataset_size'), '25')
    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request.dataset_names).toEqual(['ds_a'])
    expect(request.max_dataset_size).toBe(25)
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({
        target_name: 'target-a',
        techniques: ['default_technique'],
        dataset_names: ['ds_a'],
        max_dataset_size: 25,
        include_baseline: true,
      }),
      expect.any(AbortSignal),
    ))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('labels')
  })

  it('restores dataset defaults and removes the estimate and launch override', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('dataset-ds_a')

    await user.click(screen.getByTestId('dataset-ds_a'))
    await user.click(screen.getByTestId('dataset-harmbench'))
    expect(screen.getByTestId('restore-default-datasets')).toBeEnabled()
    await user.click(screen.getByTestId('restore-default-datasets'))
    expect(screen.getByTestId('dataset-harmbench')).toBeChecked()
    expect(screen.getByTestId('dataset-ds_a')).not.toBeChecked()

    await user.click(screen.getByTestId('launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('dataset_names')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.not.objectContaining({ dataset_names: expect.anything() }),
      expect.any(AbortSignal),
    ))
  })

  it('requires one dataset when the scenario declares defaults', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('dataset-harmbench')

    await user.click(screen.getByTestId('dataset-harmbench'))

    expect(screen.getAllByText('Select at least one dataset.')).not.toHaveLength(0)
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('keeps scenario defaults usable when the dataset catalog fails', async () => {
    mockListDatasets.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 503, data: { detail: 'Catalog unavailable' } },
    })
    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByTestId('dataset-catalog-error')).toHaveTextContent(
      'Registered datasets couldn’t be loaded. Scenario defaults remain available. Catalog unavailable',
    )
    expect(screen.getByTestId('dataset-harmbench')).toBeChecked()
    expect(screen.getByTestId('launch-scenario-btn')).not.toBeDisabled()
  })

  it('shows a loading state without hiding known scenario defaults', async () => {
    mockListDatasets.mockReturnValueOnce(new Promise(() => {}))
    renderDetail('/scenarios/foundry.red_team_agent')

    expect(await screen.findByTestId('dataset-catalog-loading')).toBeInTheDocument()
    expect(screen.getByTestId('dataset-harmbench')).toBeChecked()
  })

  it('exposes the bounded dataset picker to keyboard and assistive technology', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('dataset-ds_a')

    expect(screen.getByRole('group', { name: 'Datasets' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Search datasets' })).toBeInTheDocument()
    const dataset = screen.getByRole('checkbox', { name: 'ds_a' })
    dataset.focus()
    await user.keyboard('[Space]')
    expect(dataset).toBeChecked()
  })

  it('rejects a non-positive-integer max dataset size', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    expect(screen.getByText(/Maximum times to resume the scenario after an exception/)).toBeInTheDocument()
    expect(screen.queryByText(/separate from Adaptive trying another technique/)).not.toBeInTheDocument()
    await user.type(screen.getByTestId('advanced-max_dataset_size'), '0')

    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('blocks signed and decimal dataset-size input before it changes the controlled value', async () => {
    const scenario = makeAdaptiveScenario()
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockImplementation(
      async (_scenarioName, request: ScenarioRunSizeEstimateRequest) =>
        makeFullyCompatibleAdaptiveEstimateForRequest(scenario, request),
    )
    const user = userEvent.setup()
    renderDetail('/scenarios/adaptive.text_adaptive')
    await screen.findByTestId('scenario-target-select')
    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    const input = screen.getByTestId('advanced-max_dataset_size')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalled())
    const requestCount = mockEstimateRun.mock.calls.length

    await user.clear(input)
    await user.type(input, '-8')
    expect(input).toHaveValue(null)
    expect(input).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByText('Enter a whole number of 1 or more.')).toBeInTheDocument()
    await new Promise((resolve) => window.setTimeout(resolve, 350))
    expect(mockEstimateRun).toHaveBeenCalledTimes(requestCount)

    await user.click(input)
    await user.paste('1.5')
    expect(input).toHaveValue(null)
    expect(screen.getByRole('complementary', { name: 'Run preview' }))
      .toHaveTextContent('4 per dataset (scenario default)')
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
  })

  it('validates advanced concurrency and retry bounds before launching', async () => {
    const user = userEvent.setup()
    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByRole('button', { name: 'Advanced options' }))
    fireEvent.change(screen.getByTestId('max-concurrency-input'), { target: { value: '500' } })
    fireEvent.blur(screen.getByTestId('max-concurrency-input'))

    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(screen.getByText(
      'Max concurrency must be an integer from 1 to 100.',
    )).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('sends the exact RunScenarioRequest payload and attaches labels automatically', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr-1' })

    renderDetail('/scenarios/foundry.red_team_agent', { labels: { operator: 'roakey', operation: 'op1' } })
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalledTimes(1))
    expect(mockStartRun).toHaveBeenCalledWith({
      scenario_name: 'foundry.red_team_agent',
      target_name: 'target-a',
      techniques: ['default_technique'],
      max_concurrency: 10,
      max_retries: 0,
      include_baseline: true,
      labels: { operator: 'roakey', operation: 'op1' },
    })
  })

  it('sends only prompt_sending for the Jailbreak regression and displays the backend total of 8', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(
      makeScenario({
        scenario_name: 'airt.jailbreak',
        scenario_type: 'Jailbreak',
        description: 'Runs jailbreak templates.',
        default_technique: 'default',
        default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
        aggregate_techniques: ['default'],
        aggregate_technique_expansions: {
          default: ['prompt_sending', 'jailbreak_system_prompt'],
        },
        all_techniques: ['prompt_sending', 'jailbreak_system_prompt', 'flip'],
        default_datasets: ['harmbench'],
        include_baseline_by_default: true,
        supported_parameters: [
          {
            name: 'num_jailbreaks',
            type_name: 'int',
            required: false,
            default: null,
            choices: null,
            is_list: false,
          },
          {
            name: 'num_jailbreak_attempts',
            type_name: 'int',
            required: false,
            default: '1',
            choices: null,
            is_list: false,
          },
        ],
      }),
    )
    mockEstimateRun.mockResolvedValue({
      version: 1,
      status: 'exact',
      total_attack_count: 8,
      minimum_attack_count: null,
      maximum_attack_count: null,
      condition: null,
      components: [
        {
          label: 'Prompt sending',
          count: 8,
          factors: [
            { label: 'selected seed groups', count: 4 },
            { label: 'concrete techniques', count: 1 },
            { label: 'jailbreak templates', count: 2 },
            { label: 'attempts', count: 1 },
          ],
          is_baseline: false,
          note: null,
        },
      ],
      datasets: [
        {
          name: 'harmbench',
          kind: 'dataset',
          logical_seed_group_count: 5,
          selected_seed_group_count: 4,
          configured_caps: [
            {
              label: 'Jailbreak templates',
              count: 2,
              configured_on: 'configuration',
              dataset_name: null,
            },
          ],
          selection_note: 'One incompatible group is excluded.',
        },
      ],
      adaptive_details: null,
      note: 'The planned total is authoritative.',
      retries_included: false,
    })

    renderDetail('/scenarios/airt.jailbreak')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('technique-mode-custom'))
    await user.click(screen.getByTestId('technique-jailbreak_system_prompt'))
    await user.clear(screen.getByTestId('scenario-param-num_jailbreaks'))
    await user.type(screen.getByTestId('scenario-param-num_jailbreaks'), '2')
    await user.clear(screen.getByTestId('scenario-param-num_jailbreak_attempts'))
    await user.type(screen.getByTestId('scenario-param-num_jailbreak_attempts'), '1')
    await user.click(screen.getByTestId('baseline-checkbox'))

    const expectedRunRequest = {
      scenario_name: 'airt.jailbreak',
      target_name: 'target-a',
      techniques: ['prompt_sending'],
      max_concurrency: 10,
      max_retries: 0,
      include_baseline: false,
      labels: { operator: 'roakey' },
      scenario_params: {
        num_jailbreaks: 2,
        num_jailbreak_attempts: 1,
      },
    }
    const expectedEstimateRequest = {
      target_name: 'target-a',
      techniques: ['prompt_sending'],
      include_baseline: false,
      scenario_params: {
        num_jailbreaks: 2,
        num_jailbreak_attempts: 1,
      },
    }

    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'airt.jailbreak',
      expectedEstimateRequest,
      expect.any(AbortSignal),
    ))
    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('prompt_sending')).toBeInTheDocument()
    expect(within(preview).getByText('harmbench')).toBeInTheDocument()
    expect(within(preview).getByText('Not included')).toBeInTheDocument()
    expect(within(preview).getByRole('group', {
      name: '1 technique multiplied by 4 objectives multiplied by 2 jailbreak templates multiplied by 1 attempt equals 8 planned attacks.',
    })).toBeInTheDocument()
    expect(within(preview).getByText('4 objectives from harmbench · 5 available')).toBeInTheDocument()
    expect(within(preview).getByText('Jailbreak templates: 2')).toBeInTheDocument()
    expect(within(preview).queryByText(/logical seed groups|selected seed groups/i)).not.toBeInTheDocument()

    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalledTimes(1))
    expect(mockStartRun).toHaveBeenCalledWith(expectedRunRequest)
    expect(mockStartRun.mock.calls[0][0].techniques).not.toContain('default')
    expect(expectedEstimateRequest.techniques).toEqual(expectedRunRequest.techniques)
    expect(expectedEstimateRequest.scenario_params).toEqual(expectedRunRequest.scenario_params)
    expect(expectedEstimateRequest.include_baseline).toBe(expectedRunRequest.include_baseline)
    expect(expectedEstimateRequest).not.toHaveProperty('labels')
  })

  it('navigates to the scanner-history route with the encoded run id on success', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr/1' })

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('launch-scenario-btn'))

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        '/scanner-history/sr%2F1',
        expect.objectContaining({ state: expect.objectContaining({ scenarioName: 'foundry.red_team_agent' }) }),
      ),
    )
  })

  it('shows an API error in a MessageBar and re-enables the button on failure', async () => {
    const user = userEvent.setup()
    mockStartRun.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 400, data: { detail: 'Invalid target' } },
    })

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findByText('Invalid target')).toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).not.toBeDisabled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('guards against a duplicate submit from a fast double click', async () => {
    let resolveStartRun: (value: { scenario_result_id: string }) => void = () => {}
    mockStartRun.mockReturnValue(
      new Promise((resolve) => {
        resolveStartRun = resolve
      }),
    )

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const button = screen.getByTestId('launch-scenario-btn')
    // Fire two rapid clicks without waiting between them (userEvent.click awaits internally,
    // so dispatch native clicks to simulate a true double-click within one tick).
    act(() => {
      button.click()
      button.click()
    })

    await waitFor(() => expect(mockStartRun).toHaveBeenCalledTimes(1))
    resolveStartRun({ scenario_result_id: 'sr-1' })
    await waitFor(() => expect(button).not.toBeDisabled())
  })

  it('preserves entered values and preview content after a failed submission', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(
      makeScenario({
        supported_parameters: [
          {
            name: 'attempts',
            type_name: 'int',
            required: false,
            default: 1,
            choices: null,
            is_list: false,
          },
        ],
      }),
    )
    mockStartRun.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 400, data: { detail: 'boom' } },
    })

    renderDetail('/scenarios/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await user.click(screen.getByTestId('technique-mode-custom'))
    await user.clear(screen.getByTestId('scenario-param-attempts'))
    await user.type(screen.getByTestId('scenario-param-attempts'), '3')
    await user.click(screen.getByTestId('launch-scenario-btn'))

    await screen.findByText('boom')
    expect(screen.getByTestId('scenario-target-select')).toHaveValue('target-b')
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('scenario-param-attempts')).toHaveValue(3)

    const preview = screen.getByRole('complementary', { name: 'Run preview' })
    expect(within(preview).getByText('target-b')).toBeInTheDocument()
    expect(within(preview).getByText('crescendo')).toBeInTheDocument()
    expect(within(preview).getByText('harmbench')).toBeInTheDocument()
    expect(within(preview).getByText('3')).toBeInTheDocument()
  })
})
