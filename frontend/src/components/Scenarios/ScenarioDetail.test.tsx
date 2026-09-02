import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { scenariosApi, targetsApi } from '@/services/api'
import type {
  RegisteredScenario,
  ScenarioRunSizeEstimateResponse,
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
}))

const mockGetScenario = scenariosApi.getScenario as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock
const mockStartRun = scenariosApi.startRun as jest.Mock
const mockListTargets = targetsApi.listTargets as jest.Mock

const mockNavigate = jest.fn()
const RAW_IMAGE_HTML = ['<', 'img src=x onerror="alert(1)">'].join('')

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

function makeScenario(overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  const description = overrides.description ?? 'Red teams a target.'
  const defaultTechnique = overrides.default_technique ?? 'default'
  const aggregateTechniques = overrides.aggregate_techniques ?? ['all', 'default']
  const defaultTechniques = overrides.default_techniques
    ?? (aggregateTechniques.includes(defaultTechnique) ? ['default_technique'] : [defaultTechnique])
  const allTechniques = overrides.all_techniques ?? ['default_technique', 'crescendo']
  const techniqueSummaries = overrides.technique_summaries ?? allTechniques.map((name) => ({
    name,
    description: `${name} description.`,
    tags: name === 'default_technique' ? ['default', 'single_turn'] : ['multi_turn'],
  }))
  return {
    scenario_name: 'foundry.red_team_agent',
    scenario_type: 'RedTeamAgentScenario',
    scenario_version: 1,
    aggregate_technique_expansions: overrides.aggregate_technique_expansions
      ?? Object.fromEntries(
        aggregateTechniques.map((name) => [name, name === defaultTechnique ? defaultTechniques : []]),
      ),
    all_techniques: allTechniques,
    technique_summaries: techniqueSummaries,
    default_datasets: ['harmbench'],
    baseline_policy: 'enabled',
    include_baseline_by_default: true,
    supported_parameters: [],
    default_run_size: {
      estimated_attack_count: null,
      components: [],
      datasets: [],
      note: 'Default sizing is unavailable.',
    },
    ...overrides,
    description,
    description_markdown: overrides.description_markdown ?? description,
    default_technique: defaultTechnique,
    default_techniques: defaultTechniques,
    aggregate_techniques: aggregateTechniques,
  }
}

function makeTarget(name: string, modelName?: string): TargetInstance {
  return {
    target_registry_name: name,
    identifier: {
      class_name: 'OpenAIChatTarget',
      hash: `${name}-hash`,
      model_name: modelName,
    },
  }
}

function makeEstimate(total: number | null): ScenarioRunSizeEstimateResponse {
  return {
    estimated_attack_count: total,
    minimum_attack_count: total === null ? 8 : null,
    maximum_attack_count: total === null ? 12 : null,
    components: total === null
      ? [{ label: 'Possible attacks', count: 12, is_baseline: false, note: null }]
      : [
          {
            label: 'Configured attacks',
            count: total,
            is_baseline: false,
            note: null,
          },
        ],
    datasets: [],
    note: null,
  }
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
            path="/:catalog/:scenarioName"
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
    mockStartRun.mockReset()
    mockListTargets.mockResolvedValue({
      items: [makeTarget('target-a'), makeTarget('target-b')],
      pagination: { limit: 200, has_more: false },
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
    renderDetail('/scanner/foundry.red_team_agent')
    expect(screen.getByText('Loading scenario...')).toBeInTheDocument()
  })

  it('decodes the scenario name from the URL exactly once', async () => {
    renderDetail('/scanner/foundry.red_team_agent');
    await screen.findByTestId('scenario-target-select')
    expect(mockGetScenario).toHaveBeenCalledWith('foundry.red_team_agent')
  })

  it('decodes a slash-bearing encoded scenario name back to the original', async () => {
    renderDetail('/scanner/foundry%2Fred_team_agent')
    await waitFor(() => expect(mockGetScenario).toHaveBeenCalledWith('foundry/red_team_agent'))
    await screen.findByTestId('scenario-target-select')
  })

  it('preserves a literal percent sequence in a scenario registry name', async () => {
    renderDetail('/scanner/discount%2550')
    await waitFor(() => expect(mockGetScenario).toHaveBeenCalledWith('discount%50'))
    await screen.findByTestId('scenario-target-select')
  })

  it('handles a malformed percent sequence without throwing during render', async () => {
    const consoleWarn = jest.spyOn(console, 'warn').mockImplementation(() => {})
    mockGetScenario.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'not found' } },
    })
    renderDetail('/scanner/%zz')
    expect(await screen.findByTestId('scenario-not-found')).toBeInTheDocument()
    expect(mockGetScenario).toHaveBeenCalledWith('%zz')
    consoleWarn.mockRestore()
  })

  it('shows a distinct not-found state for a 404, with a link back to the catalog', async () => {
    mockGetScenario.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 404, data: { detail: 'not found' } },
    })

    renderDetail('/scanner/missing.scenario')

    expect(await screen.findByTestId('scenario-not-found')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /back to scanners/i })).toHaveAttribute('href', '/scanner')
    expect(screen.queryByTestId('scenario-error')).not.toBeInTheDocument()
  })

  it('shows a generic error state with retry for a non-404 failure', async () => {
    const user = userEvent.setup()
    mockGetScenario
      .mockRejectedValueOnce({ isAxiosError: true, response: { status: 500, data: { detail: 'boom' } } })
      .mockResolvedValueOnce(makeScenario())

    renderDetail('/scanner/foundry.red_team_agent')

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
    mockEstimateRun.mockResolvedValueOnce(makeEstimate(8))

    renderDetail('/scanner/foundry.red_team_agent', { onNavigate })
    await flushRenderedPromises()
    await advanceTimers(300)

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
    renderDetail('/scanner/foundry.red_team_agent', { activeTarget: makeTarget('target-b') })

    expect(await screen.findByTestId('scenario-target-select')).toHaveValue('target-b')
  })

  it('shows model names in target options without another request', async () => {
    mockListTargets.mockResolvedValueOnce({
      items: [makeTarget('target-a', 'gpt-4o'), makeTarget('target-b')],
      pagination: { limit: 200, has_more: false },
    })

    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByRole('option', { name: 'target-a (gpt-4o)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'target-b' })).toBeInTheDocument()
    expect(mockListTargets).toHaveBeenCalledTimes(1)
  })

  it('defaults the target selector to the first fetched target when there is no matching active target', async () => {
    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByTestId('scenario-target-select')).toHaveValue('target-a')
  })

  it('shows the configuration, estimate, and launch sections before the preview dialog', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByRole('form', { name: 'Scenario run configuration' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Scenario description' })).toBeInTheDocument()
    expect(screen.getByTestId('run-estimate')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Launch scan' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()

    const preview = await openRunPreview(user)
    expect(preview).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
    await user.click(within(preview).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()
  })

  it('debounces preview requests and aborts the superseded request', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    renderDetail('/scanner/foundry.red_team_agent')
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
    let resolveFirst: (estimate: ScenarioRunSizeEstimateResponse) => void = () => {}
    let resolveSecond: (estimate: ScenarioRunSizeEstimateResponse) => void = () => {}
    mockEstimateRun
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveFirst = resolve
      }))
      .mockReturnValueOnce(new Promise((resolve) => {
        resolveSecond = resolve
      }))

    renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await advanceTimers(300)

    resolveSecond(makeEstimate(12))
    await flushRenderedPromises()
    const estimate = screen.getByTestId('run-estimate')
    expect(within(estimate).getByText('12')).toBeInTheDocument()

    resolveFirst(makeEstimate(8))
    await flushRenderedPromises()
    expect(within(estimate).getByText('12')).toBeInTheDocument()
    expect(within(estimate).queryByText('8')).not.toBeInTheDocument()
  })

  it('keeps the last good estimate and entered state after a transient preview failure', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockEstimateRun
      .mockResolvedValueOnce(makeEstimate(8))
      .mockRejectedValueOnce({
        isAxiosError: true,
        response: { status: 503, data: { detail: 'Preview service unavailable' } },
      })

    renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()
    expect(within(screen.getByTestId('run-estimate')).getByText('8')).toBeInTheDocument()

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await advanceTimers(300)
    await flushRenderedPromises()

    const estimate = screen.getByTestId('run-estimate')
    expect(within(estimate).getByText('8')).toBeInTheDocument()
    expect(within(estimate).getByText('Preview service unavailable')).toBeInTheDocument()
    expect(screen.getByTestId('scenario-target-select')).toHaveValue('target-b')
    expect(screen.getByTestId('launch-scenario-btn')).not.toBeDisabled()
  })

  it('does not request a preview while the custom technique selection is empty', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()

    await user.click(screen.getByTestId('technique-default_technique'))
    await advanceTimers(300)

    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(within(screen.getByTestId('run-estimate')).getByText('Unavailable')).toBeInTheDocument()
  })

  it('renders a backend conditional estimate without inventing a total', async () => {
    jest.useFakeTimers()
    mockEstimateRun.mockResolvedValue(makeEstimate(null))
    renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)
    await flushRenderedPromises()

    const estimate = screen.getByTestId('run-estimate')
    expect(within(estimate).getByText('8-12')).toBeInTheDocument()
  })

  it('renders MyST literals through the shared safe Markdown renderer', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        description: 'Configure this scenario.',
        description_markdown: `Set \`\`num_jailbreaks\`\`.\n\n${RAW_IMAGE_HTML}unsafe`,
      }),
    )
    renderDetail('/scanner/foundry.red_team_agent')

    const description = await screen.findByTestId('scenario-detail-description')
    expect(within(description).getByText('num_jailbreaks').tagName).toBe('CODE')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
    expect(
      within(description).getByText((content: string) => content.includes(`${RAW_IMAGE_HTML}unsafe`)),
    ).toBeInTheDocument()
  })

  it('initializes the individual techniques from the resolved defaults', async () => {
    renderDetail('/scanner/foundry.red_team_agent')

    await screen.findByTestId('scenario-target-select')
    expect(screen.getByTestId('technique-default_technique')).toBeChecked()
    expect(screen.getByTestId('technique-crescendo')).not.toBeChecked()
    expect(screen.queryByText('Aggregate preset')).not.toBeInTheDocument()
    expect(screen.queryByText('Backend-resolved preset members')).not.toBeInTheDocument()
  })

  it('shows technique descriptions and tags', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        default_technique: 'default',
        default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
        aggregate_techniques: ['default'],
        aggregate_technique_expansions: {
          default: ['prompt_sending', 'jailbreak_system_prompt'],
        },
        all_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
        technique_summaries: [
          {
            name: 'prompt_sending',
            description: 'Sends the objective directly.',
            tags: ['default', 'single_turn'],
          },
          {
            name: 'jailbreak_system_prompt',
            description: 'Places the jailbreak in the system prompt.',
            tags: ['default', 'single_turn'],
          },
        ],
      }),
    )

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByText('Sends the objective directly.')).toBeInTheDocument()
    expect(screen.getByText('Places the jailbreak in the system prompt.')).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Clear Recommended techniques' })).toHaveLength(2)
    expect(screen.getAllByRole('button', { name: 'Clear Single-turn techniques' })).toHaveLength(3)
  })

  it('renders only concrete techniques and de-duplicates their names', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        aggregate_techniques: ['default_technique', 'all_garak'],
        all_techniques: ['default_technique', 'crescendo', 'prompt_sending', 'all_garak'],
      }),
    )
    const user = userEvent.setup()

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.queryByTestId('technique-all_garak')).not.toBeInTheDocument()
    expect(screen.getAllByTestId('technique-crescendo')).toHaveLength(1)

    await user.click(screen.getByTestId('technique-crescendo'))
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()

    await user.click(screen.getByTestId('technique-prompt_sending'))
    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request.techniques).toEqual(['crescendo', 'prompt_sending'])
    expect(new Set(request.techniques).size).toBe(request.techniques.length)
  })

  it('selects and clears all members of a tag', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({
        default_techniques: ['default_technique'],
        all_techniques: ['default_technique', 'crescendo', 'many_shot'],
        technique_summaries: [
          { name: 'default_technique', description: 'Direct attack.', tags: ['single_turn'] },
          { name: 'crescendo', description: 'Escalating attack.', tags: ['multi_turn'] },
          { name: 'many_shot', description: 'Many-shot attack.', tags: ['multi_turn'] },
        ],
      }),
    )
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getAllByRole('button', { name: 'Select Multi-turn techniques' })[0])
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()
    expect(screen.getByTestId('technique-many_shot')).toBeChecked()

    await user.click(screen.getAllByRole('button', { name: 'Clear Multi-turn techniques' })[0])
    expect(screen.getByTestId('technique-crescendo')).not.toBeChecked()
    expect(screen.getByTestId('technique-many_shot')).not.toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).toBeChecked()
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
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByTestId('technique-prompt_sending')).toBeChecked()
    await user.click(screen.getByTestId('technique-crescendo'))
    expect(screen.getByTestId('technique-prompt_sending')).toBeChecked()
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()

    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].techniques).toEqual(['prompt_sending', 'crescendo'])
  })

  it('keeps an explicit invalid custom state when the last concrete technique is removed', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('technique-default_technique'))

    expect(await screen.findByRole('alert')).toHaveTextContent('Select at least one attack technique.')
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('launch-scenario-btn')).toBeDisabled()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('defaults the baseline checkbox from include_baseline_by_default when enabled, and allows editing', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const checkbox = screen.getByTestId('baseline-checkbox')
    expect(checkbox).toBeChecked()
    expect(checkbox).toHaveAccessibleName('baseline')

    await user.click(checkbox)
    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].include_baseline).toBe(false)
  })

  it('includes the baseline when a shared tag selects or clears its members', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getAllByRole('button', { name: 'Clear Single-turn techniques' })[0])
    expect(screen.getByTestId('baseline-checkbox')).not.toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()

    await user.click(screen.getAllByRole('button', { name: 'Select Single-turn techniques' })[0])
    expect(screen.getByTestId('baseline-checkbox')).toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).toBeChecked()
  })

  it('defaults the baseline checkbox to unchecked when the policy is disabled with include_baseline_by_default false', async () => {
    mockGetScenario.mockResolvedValue(
      makeScenario({ baseline_policy: 'disabled', include_baseline_by_default: false }),
    )
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    expect(screen.getByTestId('baseline-checkbox')).not.toBeChecked()
  })

  it('disables and forces the baseline checkbox false when the policy is forbidden', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({ baseline_policy: 'forbidden' }))
    const user = userEvent.setup()

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const checkbox = screen.getByTestId('baseline-checkbox')
    expect(checkbox).toBeDisabled()
    expect(checkbox).not.toBeChecked()

    await confirmRunPreview(user)
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

    renderDetail('/scanner/foundry.red_team_agent')
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
    const user = userEvent.setup()

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    // A number-typed HTML input rejects non-numeric characters outright, so a
    // decimal (a valid *number* but not a valid *integer*) exercises the same
    // coercion/validation path a real user could actually trigger.
    fireEvent.change(screen.getByTestId('scenario-param-iterations'), { target: { value: '1.5' } })
    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findByRole('alert')).toHaveTextContent('iterations must be an integer.')
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('omits the dataset override and max dataset size when left blank, sending default concurrency/retries', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request).not.toHaveProperty('dataset_names')
    expect(request).not.toHaveProperty('max_dataset_size')
    expect(request.max_concurrency).toBe(10)
    expect(request.max_retries).toBe(0)
  })

  it('shows the combined configured dataset size without submitting it as an override', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValueOnce(
      makeScenario({
        default_run_size: {
          estimated_attack_count: null,
          components: [],
          datasets: [
            {
              name: 'harmbench',
              kind: 'dataset',
              logical_seed_group_count: 400,
              selected_seed_group_count: 4,
              configured_caps: [
                {
                  label: 'per-dataset cap',
                  count: 4,
                  configured_on: 'dataset',
                  dataset_name: 'harmbench',
                },
              ],
              selection_note: 'The default selection uses 4 of 400 logical seed groups.',
            },
            {
              name: 'adv_bench',
              kind: 'dataset',
              logical_seed_group_count: 300,
              selected_seed_group_count: 4,
              configured_caps: [
                {
                  label: 'per-dataset cap',
                  count: 4,
                  configured_on: 'dataset',
                  dataset_name: 'adv_bench',
                },
              ],
              selection_note: 'The default selection uses 4 of 300 logical seed groups.',
            },
          ],
          note: null,
        },
      }),
    )

    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByRole('heading', { name: 'Parameters' })).toBeInTheDocument()
    expect(screen.queryByText('Advanced options')).not.toBeInTheDocument()
    expect(screen.getByTestId('max-dataset-size-input')).toHaveValue(8)
    expect(screen.getByText(
      'The scenario default is 8. Edit it to override the default.',
    )).toBeInTheDocument()
    const estimate = screen.getByTestId('run-estimate')
    expect(within(estimate).getByText('Dataset size')).toBeInTheDocument()
    expect(within(estimate).getByText('8')).toBeInTheDocument()
    expect(within(estimate).getByText('Number techniques')).toBeInTheDocument()
    expect(within(estimate).getByText('2')).toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalled())
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')

    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('max_dataset_size')
  })

  it('includes dataset overrides and filters when provided', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.type(screen.getByTestId('dataset-override-input'), 'ds_a, ds_b')
    await user.type(screen.getByTestId('max-dataset-size-input'), '25')
    await user.type(screen.getByTestId('harm-categories-filter-input'), 'cyber, violence')
    await user.type(screen.getByTestId('data-types-filter-input'), 'text, image_path')
    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request.dataset_names).toEqual(['ds_a', 'ds_b'])
    expect(request.max_dataset_size).toBe(25)
    expect(request.dataset_filters).toEqual({
      harm_categories: ['cyber', 'violence'],
      data_types: ['text', 'image_path'],
    })
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({
        target_name: 'target-a',
        techniques: ['default_technique'],
        dataset_names: ['ds_a', 'ds_b'],
        max_dataset_size: 25,
        dataset_filters: {
          harm_categories: ['cyber', 'violence'],
          data_types: ['text', 'image_path'],
        },
        include_baseline: true,
      }),
      expect.any(AbortSignal),
    ))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('labels')
  })

  it('rejects a non-positive-integer max dataset size', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.type(screen.getByTestId('max-dataset-size-input'), '0')
    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Max dataset size must be a positive integer.',
    )
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('validates advanced concurrency and retry bounds before launching', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    fireEvent.change(screen.getByTestId('max-concurrency-input'), { target: { value: '500' } })
    fireEvent.blur(screen.getByTestId('max-concurrency-input'))
    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Max concurrency must be an integer from 1 to 100.',
    )
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('sends the exact RunScenarioRequest payload and attaches labels automatically', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr-1' })

    renderDetail('/scanner/foundry.red_team_agent', { labels: { operator: 'roakey', operation: 'op1' } })
    await screen.findByTestId('scenario-target-select')

    await confirmRunPreview(user)

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

  it('uses effective Jailbreak defaults and sends only prompt_sending', async () => {
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
      estimated_attack_count: 8,
      components: [
        {
          label: 'Prompt sending',
          count: 8,
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
      effective_parameters: {
        num_jailbreaks: 2,
        num_jailbreak_attempts: 1,
      },
      note: 'The backend total is authoritative.',
    })

    renderDetail('/scanner/airt.jailbreak')
    await screen.findByTestId('scenario-target-select')

    await user.click(screen.getByTestId('technique-jailbreak_system_prompt'))
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
        num_jailbreak_attempts: 1,
      },
    }
    const expectedEstimateRequest = {
      target_name: 'target-a',
      techniques: ['prompt_sending'],
      include_baseline: false,
      scenario_params: {
        num_jailbreak_attempts: 1,
      },
    }

    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'airt.jailbreak',
      expectedEstimateRequest,
      expect.any(AbortSignal),
    ))
    const estimate = screen.getByTestId('run-estimate')
    expect(within(estimate).getByText('num_jailbreaks').parentElement).toHaveTextContent(
      'num_jailbreaks2',
    )
    const preview = await openRunPreview(user)
    expect(within(preview).getByText('target-a')).toBeInTheDocument()
    expect(within(preview).getByText('prompt_sending')).toBeInTheDocument()
    expect(within(preview).getByText('harmbench')).toBeInTheDocument()
    expect(within(preview).getByText('num_jailbreaks').parentElement).toHaveTextContent(
      'num_jailbreaks2',
    )
    expect(within(preview).getByText('8 attacks')).toBeInTheDocument()
    expect(within(preview).getByText('Prompt sending: 8 = 8')).toBeInTheDocument()
    expect(within(preview).queryByText('Backend estimate')).not.toBeInTheDocument()
    expect(within(preview).queryByText('Current configuration')).not.toBeInTheDocument()

    await user.click(screen.getByTestId('confirm-launch-scenario-btn'))

    await waitFor(() => expect(mockStartRun).toHaveBeenCalledTimes(1))
    expect(mockStartRun).toHaveBeenCalledWith(expectedRunRequest)
    expect(mockStartRun.mock.calls[0][0].techniques).not.toContain('default')
    expect(expectedEstimateRequest.techniques).toEqual(expectedRunRequest.techniques)
    expect(expectedEstimateRequest.scenario_params).toEqual(expectedRunRequest.scenario_params)
    expect(expectedEstimateRequest.include_baseline).toBe(expectedRunRequest.include_baseline)
    expect(expectedEstimateRequest).not.toHaveProperty('labels')
  })

  it('navigates to the scenario-history route with the encoded run id on success', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr/1' })

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await confirmRunPreview(user)

    await waitFor(() =>
      expect(mockNavigate).toHaveBeenCalledWith(
        '/scenario-history/sr%2F1',
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

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await confirmRunPreview(user)

    expect(await screen.findByText('Invalid target')).toBeInTheDocument()
    expect(screen.getByTestId('launch-scenario-btn')).not.toBeDisabled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('guards against a duplicate submit from a fast double click', async () => {
    const user = userEvent.setup()
    let resolveStartRun: (value: { scenario_result_id: string }) => void = () => {}
    mockStartRun.mockReturnValue(
      new Promise((resolve) => {
        resolveStartRun = resolve
      }),
    )

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    await openRunPreview(user)

    const button = screen.getByTestId('confirm-launch-scenario-btn')
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

  it('preserves entered values and keeps the preview open after a failed submission', async () => {
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

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await user.selectOptions(screen.getByTestId('scenario-target-select'), 'target-b')
    await user.click(screen.getByTestId('technique-default_technique'))
    await user.click(screen.getByTestId('technique-crescendo'))
    await user.clear(screen.getByTestId('scenario-param-attempts'))
    await user.type(screen.getByTestId('scenario-param-attempts'), '3')
    await confirmRunPreview(user)

    await screen.findByText('boom')
    expect(screen.getByTestId('scenario-target-select')).toHaveValue('target-b')
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('scenario-param-attempts')).toHaveValue(3)

    const preview = screen.getByRole('dialog', { name: 'Run preview' })
    expect(within(preview).getByText('target-b')).toBeInTheDocument()
    expect(within(preview).getByText('crescendo')).toBeInTheDocument()
    expect(within(preview).getByText('harmbench')).toBeInTheDocument()
    expect(within(preview).getByText('attempts').parentElement).toHaveTextContent('attempts3')
  })
})
