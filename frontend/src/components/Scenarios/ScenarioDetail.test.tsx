import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { createMemoryRouter, Link, MemoryRouter, Route, RouterProvider, Routes } from 'react-router'

import { attacksApi, convertersApi, datasetsApi, scenariosApi, targetsApi, techniquesApi } from '@/services/api'
import { useRuntime } from '@/hooks/useRuntime'
import type {
  AddMessageResponse,
  RegisteredScenario,
  ScenarioDatasetSettings,
  ScenarioRunSizeEstimateRequest,
  ScenarioRunSizeEstimateResponse,
  TargetInstance,
  TechniqueConfiguration,
  TechniqueOverride,
} from '@/types'

import ScenarioDetail from './ScenarioDetail'

jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

jest.mock('@/services/api', () => ({
  techniquesApi: { getConfiguration: jest.fn(), validateEdit: jest.fn(), listTypes: jest.fn(), createTechnique: jest.fn() },
  convertersApi: { listConverters: jest.fn() },
  attacksApi: { getConversation: jest.fn(), saveConversation: jest.fn() },
  datasetsApi: { listDatasets: jest.fn().mockResolvedValue({ items: ['harmbench', 'ds_a', 'ds_b', 'remote', 'stale-dataset'].map((name) => ({
    name, selection_key: `dataset:named:${name}`, loaded: true, provider_available: true,
    logical_examples: 8, seed_pieces: null, objectives: null, modalities: ['text', 'image_path', 'audio_path'],
    harm_categories: ['cyber', 'violence', 'other'], has_unlabeled_harm_categories: false,
  })) }) },
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
const mockUseRuntime = jest.mocked(useRuntime)
const mockAttacks = jest.mocked(attacksApi)
const mockTechniques = jest.mocked(techniquesApi)

function techniqueConfiguration(name: string): TechniqueConfiguration {
  return {
    source_name: name, source_identity: `${name}-identity`, attack_type: 'prompt_sending',
    settings: { description: `${name} settings`, params: {}, simulation: {
      num_turns: 2, adversarial_chat_system_prompt: 'Movie script\n{{ objective }}',
      simulated_target_system_prompt: 'Continue for {{ num_turns }} turns.',
      next_message_system_prompt: '{{ conversation_context }}',
    } },
    parameters: [], editable_fields: ['description', 'simulation'],
    supplied_parameters: [], read_only: { policy: 'Retained unchanged.' },
    supports_prepended_conversation: true,
  }
}

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
    uses_default_adversarial_target: true,
    supported_parameters: [],
    supported_parameter_names: overrides.supported_parameter_names ?? [
      'objective_target', 'scenario_techniques', 'technique_converters', 'dataset_config',
      'memory_labels', 'max_concurrency', 'max_retries', 'include_baseline',
      ...(overrides.supported_parameters ?? []).map((parameter) => parameter.name),
    ],
    default_run_size: {
      dataset_size: { kind: 'indeterminate', detail: 'Population configuration is not available.' },
      dataset_limit: { state: 'scenario_default' },
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

function makeAdversarialTarget(name: string): TargetInstance {
  return {
    ...makeTarget(name),
    capabilities: {
      supports_multi_turn: true,
      supports_json_schema: false,
      supports_json_output: false,
      supports_system_prompt: true,
      supported_input_modalities: ['text'],
      supported_output_modalities: ['text'],
    },
  }
}

function makeEstimate(total: number | null): ScenarioRunSizeEstimateResponse {
  return {
    dataset_size: { kind: 'indeterminate', detail: 'Population configuration is not available.' },
    dataset_limit: { state: 'scenario_default' },
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
  await waitFor(() => expect(screen.getByTestId('confirm-launch-scenario-btn')).toBeEnabled())
  await user.click(screen.getByTestId('confirm-launch-scenario-btn'))
}

async function openObjectiveLimit(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(await screen.findByRole('button', { name: 'Edit Maximum objectives' }))
}

async function setObjectiveLimit(user: ReturnType<typeof userEvent.setup>, value: string): Promise<void> {
  await openObjectiveLimit(user)
  const all = screen.getByRole('checkbox', { name: 'All available for Maximum objectives' })
  if (all instanceof HTMLInputElement && all.checked) await user.click(all)
  const input = screen.getByRole('textbox', { name: 'Maximum objectives' })
  await user.clear(input)
  await user.type(input, value)
  await user.keyboard('{Escape}')
}

function renderDetail(
  path: string,
  props: Partial<React.ComponentProps<typeof ScenarioDetail>> = {},
  dataRouter = false,
) {
  const defaultProps = {
    targets: [makeTarget('target-a'), makeTarget('target-b')],
    defaultObjectiveTarget: makeTarget('target-a'),
    defaultAdversarialTarget: null,
    labels: { operator: 'roakey' },
    defaultsReady: true,
    onNavigate: jest.fn(),
  }
  const merged = { ...defaultProps, ...props }
  const renderPage = (pageProps: React.ComponentProps<typeof ScenarioDetail>): React.ReactElement => (
    <FluentProvider theme={webLightTheme}>
      {dataRouter ? <RouterProvider router={createMemoryRouter([{
        path: '/:catalog/:scenarioName', element: <ScenarioDetail {...pageProps} />,
      }], { initialEntries: [path] })} /> : <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route
            path="/:catalog/:scenarioName"
            element={<ScenarioDetail {...pageProps} />}
          />
        </Routes>
      </MemoryRouter>}
    </FluentProvider>
  )
  const view = render(renderPage(merged))
  return {
    ...view,
    updateDefaults: (defaults: Partial<React.ComponentProps<typeof ScenarioDetail>>): void => {
      view.rerender(renderPage({ ...merged, ...defaults }))
    },
  }
}

describe('ScenarioDetail', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockUseRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'first' })
    mockGetScenario.mockReset()
    mockEstimateRun.mockReset()
    mockListTargets.mockReset()
    mockStartRun.mockReset()
    mockGetScenario.mockResolvedValue(makeScenario())
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    mockStartRun.mockResolvedValue({ scenario_result_id: 'sr-default' })
    mockTechniques.getConfiguration.mockReset()
    mockTechniques.getConfiguration.mockImplementation(async (name: string) => techniqueConfiguration(name))
    mockTechniques.validateEdit.mockReset()
    mockTechniques.validateEdit.mockImplementation(async (override: TechniqueOverride) => {
      const original = techniqueConfiguration(override.source_name)
      return { override: { ...override, effective_identity: 'edited-identity' }, configuration: {
        ...original, settings: { ...original.settings, ...override.changes,
          simulation: { ...original.settings.simulation, ...override.changes.simulation } },
      } }
    })
    jest.mocked(convertersApi.listConverters).mockResolvedValue({ items: [] })
    mockListTargets.mockResolvedValue({ items: [], pagination: { limit: 200, has_more: false } })
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  describe('scanner conversation configuration', () => {
    const saved: AddMessageResponse = {
      attack: {
        attack_result_id: 'history-attack', conversation_id: 'history-conversation', attack_type: 'ManualAttack',
        objective: '', converters: [], message_count: 1, related_conversation_ids: [],
        labels: {}, created_at: '', updated_at: '', target_unbound: true,
      },
      messages: {
        conversation_id: 'history-conversation', target_response_status: null,
        messages: [{
          role: 'system', turn_number: 0, created_at: '',
          message_pieces: [{
            id: 'history-piece', original_value_data_type: 'text', original_value: 'Saved context',
            converted_value_data_type: 'text', converted_value: 'Saved context', scores: [], response_error: 'none',
          }],
        }],
      },
    }

    beforeEach(() => {
      mockGetScenario.mockResolvedValue(makeScenario({
        supported_parameters: [
          { name: 'prepended_conversation_id', type_name: 'str', required: false },
          { name: 'apply_converters_to_prepended_history', type_name: 'bool', required: false, default: 'False' },
          { name: 'max_turns', type_name: 'int', required: false },
        ],
      }))
      mockAttacks.getConversation.mockReset()
      mockAttacks.saveConversation.mockReset()
      mockAttacks.getConversation.mockResolvedValue(saved)
      mockAttacks.saveConversation.mockResolvedValue({
        ...saved, messages: { ...saved.messages, conversation_id: 'edited-conversation' },
      })
    })

    it('should save all builder turns into the scanner field without losing configuration or sending', async () => {
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute', {}, true)
      await user.type(await screen.findByRole('spinbutton', { name: 'max_turns' }), '3')
      expect(screen.getByRole('button', { name: 'Edit conversation' })).toBeDisabled()
      await user.click(screen.getByRole('button', { name: 'Add new conversation' }))
      const first = screen.getByRole('region', { name: 'Message 1' })
      await user.selectOptions(within(first).getByRole('combobox'), 'system')
      await user.type(within(first).getByRole('textbox'), 'Simulated context')
      await user.click(screen.getByRole('button', { name: 'Insert message after message 1' }))
      await user.type(within(screen.getByRole('region', { name: 'Message 2' })).getByRole('textbox'), 'Final user turn')
      expect(screen.queryByRole('button', { name: 'Send message' })).not.toBeInTheDocument()
      await user.click(screen.getByRole('button', { name: 'Add to attack' }))
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('edited-conversation')
      expect(screen.getByRole('spinbutton', { name: 'max_turns' })).toHaveValue(3)
      expect(mockAttacks.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: 'new_attack', target_registry_name: undefined, operator: 'roakey',
        messages: [
          expect.objectContaining({ role: 'system', pieces: [expect.objectContaining({ original_value: 'Simulated context' })] }),
          expect.objectContaining({ role: 'user', pieces: [expect.objectContaining({ original_value: 'Final user turn' })] }),
        ],
      }))
      expect(mockAttacks.getConversation).not.toHaveBeenCalled()
      expect(mockStartRun).not.toHaveBeenCalled()
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { max_turns: 3, prepended_conversation_id: 'edited-conversation', apply_converters_to_prepended_history: false },
      }), expect.any(Object)))
      expect(screen.getByRole('switch', { name: 'Apply converters to conversation' })).toBeEnabled()
    })

    it('should enable the converters toggle only with a conversation ID and submit its boolean choice', async () => {
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute')
      const toggle = await screen.findByRole('switch', { name: 'Apply converters to conversation' })
      const id = screen.getByRole('textbox', { name: 'prepended_conversation_id' })
      expect(toggle).not.toBeChecked()
      expect(toggle).toBeDisabled()
      expect(screen.queryByRole('combobox', { name: 'apply_converters_to_prepended_history' })).not.toBeInTheDocument()
      await user.type(id, '   ')
      expect(toggle).toBeDisabled()
      await user.clear(id)
      await user.type(id, 'history-conversation')
      expect(toggle).toBeEnabled()
      await user.click(toggle)
      expect(toggle).toBeChecked()
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { prepended_conversation_id: 'history-conversation', apply_converters_to_prepended_history: true },
      }), expect.any(Object)))
      await user.click(toggle)
      expect(toggle).not.toBeChecked()
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { prepended_conversation_id: 'history-conversation', apply_converters_to_prepended_history: false },
      }), expect.any(Object)))
      await user.clear(id)
      expect(toggle).toBeDisabled()
      expect(mockStartRun).not.toHaveBeenCalled()
    })

    it('should fill the conversation from the URL without opening an editor or launching a scan', async () => {
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute?prepended_conversation_id=history-conversation')
      const id = await screen.findByRole('textbox', { name: 'prepended_conversation_id' })
      expect(id).toHaveValue('history-conversation')
      expect(screen.getByRole('button', { name: 'Edit conversation' })).toBeEnabled()
      expect(screen.getByRole('switch', { name: 'Apply converters to conversation' })).toBeEnabled()
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(mockAttacks.getConversation).not.toHaveBeenCalled()
      expect(mockStartRun).not.toHaveBeenCalled()
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { prepended_conversation_id: 'history-conversation', apply_converters_to_prepended_history: false },
      }), expect.any(Object)))
      await user.clear(id)
      await user.type(id, 'another-conversation')
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { prepended_conversation_id: 'another-conversation', apply_converters_to_prepended_history: false },
      }), expect.any(Object)))
      expect(id).toHaveValue('another-conversation')
    })

    it('should fill a new conversation when the same scanner is opened with a different URL parameter', async () => {
      const user = userEvent.setup()
      render(<FluentProvider theme={webLightTheme}>
        <MemoryRouter initialEntries={['/scanner/technique.execute?prepended_conversation_id=first-conversation']}>
          <Link to="/scanner/technique.execute?prepended_conversation_id=second-conversation">Use another conversation</Link>
          <Routes><Route path="/scanner/:scenarioName" element={
            <ScenarioDetail targets={[]} defaultObjectiveTarget={null} defaultAdversarialTarget={null}
              labels={{ operator: 'roakey' }} defaultsReady onNavigate={jest.fn()} />
          } /></Routes>
        </MemoryRouter>
      </FluentProvider>)
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('first-conversation')
      await user.click(screen.getByRole('link', { name: 'Use another conversation' }))
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('second-conversation')
      expect(mockStartRun).not.toHaveBeenCalled()
    })

    it.each([
      '?prepended_conversation_id=%20%20',
      '?prepended_conversation_id=first&prepended_conversation_id=second',
    ])('should leave the conversation unset for an empty or ambiguous URL parameter: %s', async (query: string) => {
      renderDetail(`/scanner/technique.execute${query}`)
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('')
      expect(screen.getByRole('button', { name: 'Edit conversation' })).toBeDisabled()
      expect(mockStartRun).not.toHaveBeenCalled()
    })

    it('should decode the conversation URL parameter', async () => {
      renderDetail('/scanner/technique.execute?prepended_conversation_id=saved%2Fhistory%20%26%20context')
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('saved/history & context')
    })

    it('should preserve defaults and ignore conversation IDs that the scanner does not support', async () => {
      mockGetScenario.mockResolvedValue(makeScenario({
        supported_parameters: [{ name: 'max_turns', type_name: 'int', required: false, default: '7' }],
      }))
      renderDetail('/scanner/foundry.red_team_agent?prepended_conversation_id=history-conversation')
      expect(await screen.findByRole('spinbutton', { name: 'max_turns' })).toHaveValue(7)
      expect(screen.queryByRole('textbox', { name: 'prepended_conversation_id' })).not.toBeInTheDocument()
      await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith('foundry.red_team_agent', expect.objectContaining({
        scenario_params: { max_turns: 7 },
      }), expect.any(Object)))
    })

    it.each(['False', 'True'])('should use the declared %s converters default', async (defaultValue: string) => {
      mockGetScenario.mockResolvedValue(makeScenario({
        supported_parameters: [
          { name: 'prepended_conversation_id', type_name: 'str', required: false, default: 'history-conversation' },
          { name: 'apply_converters_to_prepended_history', type_name: 'bool', required: false, default: defaultValue },
        ],
      }))
      renderDetail('/scanner/technique.execute')
      const toggle = await screen.findByRole('switch', { name: 'Apply converters to conversation' })
      expect(toggle).toBeEnabled()
      if (defaultValue === 'True') expect(toggle).toBeChecked()
      else expect(toggle).not.toBeChecked()
    })

    it('should load the field history for editing and replace its ID with a saved copy', async () => {
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute', {}, true)
      await user.type(await screen.findByRole('textbox', { name: 'prepended_conversation_id' }), ' history-conversation ')
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      const first = await screen.findByRole('region', { name: 'Message 1' })
      expect(within(first).getByRole('textbox')).toHaveValue('Saved context')
      await user.type(within(first).getByRole('textbox'), ' changed')
      await user.click(screen.getByRole('button', { name: 'Add to attack' }))
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('edited-conversation')
      expect(mockAttacks.getConversation).toHaveBeenCalledWith('history-conversation')
      expect(mockAttacks.saveConversation).toHaveBeenCalledWith(expect.objectContaining({
        destination: 'new_attack', source_attack_result_id: 'history-attack',
        source_conversation_id: 'history-conversation', target_registry_name: undefined,
        messages: [expect.objectContaining({
          role: 'system', pieces: [expect.objectContaining({ original_value: 'Saved context changed' })],
        })],
      }))
      expect(mockStartRun).not.toHaveBeenCalled()
    })

    it('should block scan preview while history loads or its editor is open', async () => {
      let resolveConversation: (response: AddMessageResponse) => void = () => {}
      mockAttacks.getConversation.mockReturnValue(new Promise<AddMessageResponse>((resolve) => {
        resolveConversation = resolve
      }))
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute?prepended_conversation_id=history-conversation', {}, true)
      const launch = await screen.findByRole('button', { name: 'Launch scan' })
      const form = launch.closest('form')
      if (!form) throw new Error('Launch scan must submit the configuration form.')
      expect(launch).toBeEnabled()
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      expect(launch).toBeDisabled()
      await user.click(launch)
      fireEvent.submit(form)
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(mockStartRun).not.toHaveBeenCalled()

      await act(async () => { resolveConversation(saved) })
      await screen.findByRole('region', { name: 'Message 1' })
      fireEvent.submit(form)
      expect(screen.queryByText('Run preview')).not.toBeInTheDocument()
      expect(mockStartRun).not.toHaveBeenCalled()
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await screen.findByRole('textbox', { name: 'prepended_conversation_id' })
      expect(launch).toBeEnabled()
      await confirmRunPreview(user)
      expect(mockStartRun).toHaveBeenCalledTimes(1)
      expect(mockStartRun).toHaveBeenCalledWith(expect.objectContaining({
        scenario_params: expect.objectContaining({ prepended_conversation_id: 'history-conversation' }),
      }))
    })

    it('should block scan preview until the edited conversation is saved and launch with its new ID', async () => {
      let resolveSave: (response: AddMessageResponse) => void = () => {}
      mockAttacks.saveConversation.mockReturnValue(new Promise<AddMessageResponse>((resolve) => {
        resolveSave = resolve
      }))
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute?prepended_conversation_id=history-conversation', {}, true)
      const launch = await screen.findByRole('button', { name: 'Launch scan' })
      const form = launch.closest('form')
      if (!form) throw new Error('Launch scan must submit the configuration form.')
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      await user.type(within(await screen.findByRole('region', { name: 'Message 1' })).getByRole('textbox'), ' changed')
      await user.click(screen.getByRole('button', { name: 'Add to attack' }))
      expect(mockAttacks.saveConversation).toHaveBeenCalledTimes(1)
      expect(launch).toBeDisabled()
      fireEvent.submit(form)
      expect(screen.queryByText('Run preview')).not.toBeInTheDocument()
      expect(mockStartRun).not.toHaveBeenCalled()

      await act(async () => {
        resolveSave({ ...saved, messages: { ...saved.messages, conversation_id: 'edited-conversation' } })
      })
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('edited-conversation')
      expect(launch).toBeEnabled()
      await confirmRunPreview(user)
      expect(mockStartRun).toHaveBeenCalledTimes(1)
      expect(mockStartRun).toHaveBeenCalledWith(expect.objectContaining({
        scenario_params: expect.objectContaining({ prepended_conversation_id: 'edited-conversation' }),
      }))
    })

    it('should keep the configured ID when an edited draft is discarded', async () => {
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute', {}, true)
      await user.type(await screen.findByRole('textbox', { name: 'prepended_conversation_id' }), 'history-conversation')
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      await user.type(within(await screen.findByRole('region', { name: 'Message 1' })).getByRole('textbox'), ' discarded')
      await user.click(screen.getByRole('button', { name: 'Cancel' }))
      await user.click(await screen.findByRole('button', { name: 'Discard draft' }))
      expect(await screen.findByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('history-conversation')
      expect(mockAttacks.saveConversation).not.toHaveBeenCalled()
    })

    it('should report a missing conversation without opening an empty editor or changing the field', async () => {
      mockAttacks.getConversation.mockRejectedValue(new Error('Saved chat conversation not found'))
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute', {}, true)
      await user.type(await screen.findByRole('textbox', { name: 'prepended_conversation_id' }), 'missing')
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      expect(await screen.findByText('Saved chat conversation not found')).toBeInTheDocument()
      expect(screen.getByRole('textbox', { name: 'prepended_conversation_id' })).toHaveValue('missing')
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
      expect(mockAttacks.saveConversation).not.toHaveBeenCalled()
      expect(screen.getByRole('button', { name: 'Launch scan' })).toBeEnabled()
    })

    it('should keep the draft and scanner ID when saving fails', async () => {
      mockAttacks.saveConversation.mockRejectedValue(new Error('Conversation save failed'))
      const user = userEvent.setup()
      renderDetail('/scanner/technique.execute', {}, true)
      await user.type(await screen.findByRole('textbox', { name: 'prepended_conversation_id' }), 'history-conversation')
      await user.click(screen.getByRole('button', { name: 'Edit conversation' }))
      await user.type(within(await screen.findByRole('region', { name: 'Message 1' })).getByRole('textbox'), ' kept')
      await user.click(screen.getByRole('button', { name: 'Add to attack' }))
      expect(await screen.findByText('Conversation save failed')).toBeInTheDocument()
      expect(within(screen.getByRole('region', { name: 'Message 1' })).getByRole('textbox')).toHaveValue('Saved context kept')
      expect(mockStartRun).not.toHaveBeenCalled()
    })
  })

  it('blocks a legacy catalog response without support names instead of guessing common support', async () => {
    const legacyScenario = makeScenario()
    Reflect.deleteProperty(legacyScenario, 'supported_parameter_names')
    mockGetScenario.mockResolvedValue(legacyScenario)

    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByText(/catalog has no input support metadata/i)).toBeInTheDocument()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it.each([
    ['dataset_config', 'Dataset override', ['dataset_names', 'max_dataset_size', 'dataset_filters']],
    ['scenario_techniques', 'default_technique', ['techniques']],
    ['include_baseline', 'baseline', ['include_baseline']],
    ['max_concurrency', 'Max concurrency', ['max_concurrency']],
    ['max_retries', 'Max retries', ['max_retries']],
    ['memory_labels', null, ['labels']],
    ['objective_target', 'Objective Target', ['target_name']],
  ])('omits controls and request aliases for undeclared %s', async (
    name: string, label: string | null, requestFields: string[],
  ) => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const scenario = makeScenario()
    scenario.supported_parameter_names = scenario.supported_parameter_names.filter(
      (parameterName) => parameterName !== name,
    )
    // Defaults and estimate data do not establish support for an input.
    mockGetScenario.mockResolvedValue(scenario)
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()
    await advanceTimers(300)

    if (label) {
      expect(screen.queryByLabelText(label)).not.toBeInTheDocument()
    }
    if (name === 'dataset_config') {
      expect(screen.queryByLabelText('Max dataset size')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Harm categories')).not.toBeInTheDocument()
      expect(screen.queryByLabelText('Data types')).not.toBeInTheDocument()
      expect(screen.queryByRole('checkbox', { name: 'No total dataset limit' })).not.toBeInTheDocument()
    }
    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    const preview = await openRunPreview(user)
    if (name === 'dataset_config') {
      expect(within(preview).queryByText('Datasets')).not.toBeInTheDocument()
      expect(within(preview).queryByText('Dataset filters')).not.toBeInTheDocument()
    }
    await user.click(screen.getByTestId('confirm-launch-scenario-btn'))
    expect(mockStartRun).toHaveBeenCalledTimes(1)
    for (const field of requestFields) {
      expect(mockEstimateRun.mock.calls[0][1]).not.toHaveProperty(field)
      expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty(field)
    }
  })

  it('keeps baseline selection independent when attack technique selection is unsupported', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      supported_parameter_names: ['objective_target', 'include_baseline'],
      all_techniques: [],
      default_techniques: [],
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    const baseline = await screen.findByRole('checkbox', { name: 'baseline' })
    await user.click(baseline)
    await confirmRunPreview(user)

    expect(mockStartRun).toHaveBeenCalledWith({
      scenario_name: 'foundry.red_team_agent',
      target_name: 'target-a',
      include_baseline: false,
    })
    expect(screen.queryByText('Select at least one attack technique.')).not.toBeInTheDocument()
  })

  it('drops unsupported invalid drafts after catalog refresh and preserves supported values', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    const parameters = [
      { name: 'iterations', type_name: 'int', required: true, default: null, choices: null, is_list: false },
      { name: 'note', type_name: 'str', required: true, default: null, choices: null, is_list: false },
    ]
    mockGetScenario.mockResolvedValue(makeScenario({ supported_parameters: parameters }))
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    const detail = renderDetail('/scanner/foundry.red_team_agent')
    await flushRenderedPromises()
    await user.type(screen.getByLabelText(/^iterations/), '1.5')
    await user.type(screen.getByLabelText(/^note/), 'Keep this value')
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'stale-dataset (unknown)', exact: true }))
    await setObjectiveLimit(user, '0')
    await user.click(screen.getAllByRole('button', { name: 'Filter harm category cyber' })[0])
    await user.click(screen.getAllByRole('button', { name: 'Filter data type Text' })[0])
    for (const label of ['Max concurrency', 'Max retries']) {
      const input = screen.getByRole('spinbutton', { name: label })
      await user.clear(input)
      await user.type(input, '500')
      await user.tab()
    }
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    expect(screen.getByRole('alert')).toHaveTextContent('Maximum objectives must be a positive integer')

    mockGetScenario.mockResolvedValue(makeScenario({
      supported_parameter_names: ['note'],
      // A removed external descriptor must not validate or submit a stale value.
      supported_parameters: parameters,
      uses_default_adversarial_target: false,
    }))
    mockUseRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'second' })
    detail.updateDefaults({ targets: [] })
    await flushRenderedPromises()
    await user.click(screen.getByRole('button', { name: 'I have reviewed the refreshed selections' }))
    await advanceTimers(300)

    expect(screen.getByLabelText(/^note/)).toHaveValue('Keep this value')
    expect(screen.queryByLabelText(/^iterations/)).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).toEqual({
      scenario_params: { note: 'Keep this value' },
    })
    await confirmRunPreview(user)
    expect(mockStartRun).toHaveBeenCalledWith({
      scenario_name: 'foundry.red_team_agent',
      scenario_params: { note: 'Keep this value' },
    })
  })

  it('shows a loading state while fetching', () => {
    mockGetScenario.mockReturnValue(new Promise(() => {}))
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
    expect(mockGetScenario).toHaveBeenCalledTimes(2)
    expect(mockListTargets).not.toHaveBeenCalled()
  })

  it('estimates without a target and directs to the registry before launch', async () => {
    jest.useFakeTimers()
    const onNavigate = jest.fn()
    mockEstimateRun.mockResolvedValueOnce(makeEstimate(8))

    renderDetail('/scanner/foundry.red_team_agent', { targets: [], onNavigate })
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
    expect(onNavigate).toHaveBeenCalledWith('registry')
  })

  it('prefills the objective default when it is among the supplied targets', async () => {
    renderDetail('/scanner/foundry.red_team_agent', { defaultObjectiveTarget: makeTarget('target-b') })

    expect(await screen.findByTestId('scenario-target-select')).toHaveValue('target-b')
  })

  it('shows supplied target model names without fetching the target registry', async () => {
    renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a', 'gpt-4o'), makeTarget('target-b')],
    })

    expect(await screen.findByRole('option', { name: 'target-a (gpt-4o)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'target-b' })).toBeInTheDocument()
    expect(mockListTargets).not.toHaveBeenCalled()
  })

  it.each([null, makeTarget('missing-target'), {
    ...makeTarget('target-a'),
    identifier: { class_name: 'OpenAIChatTarget', hash: 'changed' },
  }])('leaves the objective selection blank without a valid default (%s)', async (defaultObjectiveTarget: TargetInstance | null) => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent', { defaultObjectiveTarget })

    expect(await screen.findByRole('combobox', { name: 'Objective Target' })).toHaveValue('')
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Select a target.')
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('initializes objective and eligible adversarial defaults from supplied targets', async () => {
    const adversary = makeAdversarialTarget('adversary')
    renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a'), makeTarget('target-b'), adversary],
      defaultObjectiveTarget: makeTarget('target-b'),
      defaultAdversarialTarget: adversary,
    })
    expect(await screen.findByRole('combobox', { name: 'Objective Target' })).toHaveValue('target-b')
    const parameters = screen.getByRole('region', { name: 'Parameters' })
    const fallback = within(parameters).getByRole('combobox', { name: 'Adversarial Target' })
    expect(within(screen.getByRole('region', { name: 'Objective' })).queryByRole(
      'combobox', { name: 'Adversarial Target' },
    )).not.toBeInTheDocument()
    const datasetsSection = screen.getByRole('region', { name: 'Datasets' })
    expect(within(datasetsSection).getByRole('table', { name: 'Selected datasets' })).toBeInTheDocument()
    expect(datasetsSection.compareDocumentPosition(parameters)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(fallback).toHaveAccessibleDescription('The registered target this scenario uses to generate attacks.')
    expect(fallback).toHaveValue('adversary')
    expect(within(fallback).queryByRole('option', { name: 'target-a' })).not.toBeInTheDocument()
  })

  it('does not replace per-form overrides or cleared values when saved defaults change', async () => {
    const user = userEvent.setup()
    const adversary = makeAdversarialTarget('adversary')
    const view = renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a'), makeTarget('target-b'), adversary],
      defaultAdversarialTarget: adversary,
    })
    const objective = await screen.findByRole('combobox', { name: 'Objective Target' })
    const fallback = screen.getByRole('combobox', { name: 'Adversarial Target' })
    await user.selectOptions(objective, 'target-b')
    await user.selectOptions(fallback, '')
    view.updateDefaults({ defaultObjectiveTarget: null, defaultAdversarialTarget: null })
    expect(objective).toHaveValue('target-b')
    view.updateDefaults({ defaultObjectiveTarget: makeTarget('target-a'), defaultAdversarialTarget: adversary })
    expect(objective).toHaveValue('target-b')
    expect(fallback).toHaveValue('')
  })

  it.each(['default', 'override', 'clear'])('uses the same %s adversarial fallback for estimate and run', async (mode: string) => {
    const user = userEvent.setup()
    const adversary = makeAdversarialTarget('adversary')
    const override = makeAdversarialTarget('override')
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a'), adversary, override],
      defaultAdversarialTarget: adversary,
    })
    const fallback = await screen.findByRole('combobox', { name: 'Adversarial Target' })
    expect(fallback).toHaveValue('adversary')
    const name = mode === 'clear' ? '' : mode === 'override' ? 'override' : 'adversary'
    await user.selectOptions(fallback, name)
    await waitFor(() => {
      expect(mockEstimateRun).toHaveBeenLastCalledWith(
        'foundry.red_team_agent',
        {
          target_name: 'target-a',
          techniques: ['default_technique'],
          include_baseline: true,
          ...(name ? { adversarial_target_name: name } : {}),
        },
        expect.any(AbortSignal),
      )
    })
    const estimateRequest = mockEstimateRun.mock.calls[mockEstimateRun.mock.calls.length - 1][1]
    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalledTimes(1))
    expect(mockStartRun.mock.calls[0][0]).toEqual({
      ...estimateRequest,
      scenario_name: 'foundry.red_team_agent',
      max_concurrency: 10,
      max_retries: 0,
      labels: { operator: 'roakey' },
    })
    if (!name) expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('adversarial_target_name')
  })

  it('ignores an ineligible adversarial default', async () => {
    renderDetail('/scanner/foundry.red_team_agent', { defaultAdversarialTarget: makeTarget('target-a') })
    expect(await screen.findByRole('combobox', { name: 'Adversarial Target' })).toHaveValue('')
  })

  it.each([
    makeAdversarialTarget('missing-adversary'),
    {
      ...makeAdversarialTarget('adversary'),
      identifier: { class_name: 'OpenAIChatTarget', hash: 'changed' },
    },
  ])('ignores an adversarial default whose exact identity is unavailable (%s)', async (defaultAdversarialTarget: TargetInstance) => {
    renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a'), makeAdversarialTarget('adversary')],
      defaultAdversarialTarget,
    })
    expect(await screen.findByRole('combobox', { name: 'Adversarial Target' })).toHaveValue('')
  })

  it('hides the default selector and omits its value for an explicit-target benchmark', async () => {
    const user = userEvent.setup()
    const adversary = makeAdversarialTarget('adversary')
    mockGetScenario.mockResolvedValue(makeScenario({
      uses_default_adversarial_target: false,
      supported_parameters: [{
        name: 'adversarial_targets',
        type_name: 'str',
        required: false,
        default: null,
        choices: null,
        is_list: true,
      }],
    }))
    mockEstimateRun.mockResolvedValue(makeEstimate(8))
    renderDetail('/scanner/foundry.red_team_agent', {
      targets: [makeTarget('target-a'), adversary],
      defaultAdversarialTarget: adversary,
    })
    await user.type(await screen.findByRole('textbox', { name: 'adversarial_targets' }), 'benchmark-a, benchmark-b')
    expect(screen.queryByRole('combobox', { name: 'Adversarial Target' })).not.toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({
        scenario_params: { adversarial_targets: ['benchmark-a', 'benchmark-b'] },
      }),
      expect.any(AbortSignal),
    ))
    expect(mockEstimateRun.mock.calls.at(-1)[1]).not.toHaveProperty('adversarial_target_name')
    const preview = await openRunPreview(user)
    expect(within(preview).queryByText('Adversarial Target')).not.toBeInTheDocument()
    await user.click(screen.getByTestId('confirm-launch-scenario-btn'))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalledWith(expect.objectContaining({
      scenario_params: { adversarial_targets: ['benchmark-a', 'benchmark-b'] },
    })))
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('adversarial_target_name')
  })

  it('hides the adversarial selector for scenarios that do not use the default target', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({ uses_default_adversarial_target: false }))
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByRole('combobox', { name: 'Objective Target' })
    expect(screen.queryByRole('combobox', { name: 'Adversarial Target' })).not.toBeInTheDocument()
    expect(screen.queryByText('The registered target this scenario uses to generate attacks.')).not.toBeInTheDocument()
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

  it('restores focus to Launch scan button when preview dialog is cancelled', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')

    const launchButton = await screen.findByTestId('launch-scenario-btn')
    const preview = await openRunPreview(user)
    expect(preview).toBeInTheDocument()

    await user.click(within(preview).getByRole('button', { name: 'Cancel' }))
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()
    await waitFor(() => {
      expect(launchButton).toHaveFocus()
    })
  })

  it('restores focus to Launch scan button when preview dialog is dismissed via Escape', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')

    const launchButton = await screen.findByTestId('launch-scenario-btn')
    const preview = await openRunPreview(user)
    expect(preview).toBeInTheDocument()

    await user.keyboard('{Escape}')
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()
    await waitFor(() => {
      expect(launchButton).toHaveFocus()
    })
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
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: { ...makeEstimate(8), dataset_size: { kind: 'bounded', value: 5 }, dataset_limit: { state: 'value', value: 5 } },
    }))
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
    expect(within(screen.getByTestId('run-estimate')).getByText('Unknown')).toBeInTheDocument()
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

  it('uses one radio selection when the backend limits the scenario to one technique', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    renderDetail('/scanner/foundry.red_team_agent')
    const selected = await screen.findByRole('radio', { name: 'default_technique' })
    const other = screen.getByRole('radio', { name: 'crescendo' })
    expect(selected).toBeChecked()
    expect(other).not.toBeChecked()
    expect(screen.queryByRole('button', { name: /Multi-turn techniques/ })).not.toBeInTheDocument()
    await user.click(other)
    expect(selected).not.toBeChecked()
    expect(other).toBeChecked()
    await user.click(other)
    expect(other).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'baseline' })).toBeChecked()
    await confirmRunPreview(user)
    expect(mockStartRun.mock.calls[0][0].techniques).toEqual(['crescendo'])
    expect(mockStartRun.mock.calls[0][0].include_baseline).toBe(true)
  })

  it('should load and edit only single-technique scenarios, then preview and launch the same local override', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    renderDetail('/scanner/technique.execute')
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    expect(mockTechniques.getConfiguration).toHaveBeenCalledWith('default_technique')
    const editor = screen.getByRole('region', { name: 'Technique Parameters' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(within(editor).queryByRole('combobox', { name: 'Attack type' })).not.toBeInTheDocument()
    expect(await within(editor).findByRole('spinbutton', { name: 'Turn count' })).toHaveValue(2)
    expect(within(editor).getByLabelText('Simulated user system prompt')).toHaveValue('Movie script\n{{ objective }}')
    await user.clear(within(editor).getByRole('spinbutton', { name: 'Turn count' }))
    await user.type(within(editor).getByRole('spinbutton', { name: 'Turn count' }), '4')
    expect(within(editor).queryByRole('button', { name: 'Apply to run' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    await screen.findByText('Edited for this run')
    expect(mockTechniques.validateEdit).toHaveBeenCalledWith({
      source_name: 'default_technique', source_identity: 'default_technique-identity',
      changes: { simulation: { num_turns: 4 } }, dependencies: {},
    })
    expect(mockTechniques.createTechnique).not.toHaveBeenCalled()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledWith(
      'foundry.red_team_agent', expect.objectContaining({
        technique_override: expect.objectContaining({ changes: { simulation: { num_turns: 4 } } }),
      }), expect.any(AbortSignal),
    ))
    const preview = await openRunPreview(user)
    expect(within(preview).getByLabelText('Technique edit preview')).toHaveTextContent('"num_turns": 4')
    await user.click(within(preview).getByRole('button', { name: 'Launch scan' }))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].technique_override).toEqual(expect.objectContaining({
      source_name: 'default_technique', effective_identity: 'edited-identity',
    }))
  })

  it('should keep inline edits when a switch is cancelled and reset to the source', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    renderDetail('/scanner/technique.execute')
    await user.type(await screen.findByLabelText('Simulated user system prompt'), ' edited')
    await screen.findByText('Edited for this run')
    expect(await screen.findByLabelText('Simulated user system prompt')).toHaveValue('Movie script\n{{ objective }} edited')
    await user.click(await screen.findByRole('radio', { name: 'crescendo' }))
    expect(await screen.findByText('Discard technique edits?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(await screen.findByRole('radio', { name: 'default_technique' })).toBeChecked()
    expect(screen.getByText('Edited for this run')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'baseline' }))
    expect(screen.getByText('Edited for this run')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Reset to original' }))
    expect(screen.queryByText('Edited for this run')).not.toBeInTheDocument()
    expect(await screen.findByLabelText('Simulated user system prompt')).toHaveValue('Movie script\n{{ objective }}')
    await user.click(screen.getByRole('radio', { name: 'crescendo' }))
    expect(screen.getByRole('radio', { name: 'crescendo' })).toBeChecked()
    await waitFor(() => expect(mockTechniques.getConfiguration).toHaveBeenCalledWith('crescendo'))
  })

  it('should show one adversarial target selector outside the technique editor', async () => {
    const user = userEvent.setup()
    const adversary = makeAdversarialTarget('adversary')
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    mockTechniques.getConfiguration.mockImplementation(async (name: string) => {
      const configuration = techniqueConfiguration(name)
      return { ...configuration, editable_fields: [...configuration.editable_fields, 'adversarial_chat'] }
    })
    renderDetail('/scanner/technique.execute', {
      targets: [makeTarget('target-a'), adversary],
    })
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    const editor = screen.getByRole('region', { name: 'Technique Parameters' })
    expect(within(editor).getByText('default_technique settings')).toBeInTheDocument()
    expect(within(editor).queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument()
    expect(within(editor).queryByRole('combobox', { name: /Adversarial target/i })).not.toBeInTheDocument()
    expect(screen.getAllByRole('combobox', { name: /Adversarial target/i })).toHaveLength(1)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Adversarial Target' }), 'adversary')
    await confirmRunPreview(user)
    expect(mockStartRun.mock.calls[0][0].adversarial_target_name).toBe('adversary')
    expect(mockStartRun.mock.calls[0][0].technique_override).toBeUndefined()
  })

  it('should show no editor or configuration request for other scenarios with one selected item', async () => {
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')
    expect(screen.queryByRole('button', { name: 'Edit technique' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Technique Parameters' })).not.toBeInTheDocument()
    expect(mockTechniques.getConfiguration).not.toHaveBeenCalled()
  })

  it('should group the launch form in technique, objective, technique parameters, parameters, and estimate order', async () => {
    mockGetScenario.mockResolvedValue(makeScenario({
      max_concrete_techniques: 1, default_datasets: [],
      supported_parameter_names: [
        'objective_target', 'scenario_techniques', 'include_baseline', 'max_concurrency', 'max_retries',
        'objective', 'prepended_conversation_id', 'apply_converters_to_prepended_history', 'retries_on_objective_failure',
      ],
      supported_parameters: [
        { name: 'objective', type_name: 'str', required: true, multiline: true },
        { name: 'prepended_conversation_id', type_name: 'str', required: false },
        { name: 'apply_converters_to_prepended_history', type_name: 'bool', required: false, default: 'False' },
        { name: 'retries_on_objective_failure', type_name: 'int', required: false, default: '0' },
      ],
    }))
    renderDetail('/scanner/technique.execute')
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    const form = screen.getByRole('form', { name: 'Scenario run configuration' })
    expect(within(form).getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'Techniques', 'Objective', 'Technique Parameters', 'Parameters', 'Run estimate',
    ])
    expect(within(screen.getByRole('region', { name: 'Objective' })).getByRole('textbox', { name: 'objective' })).toBeRequired()
    expect(within(screen.getByRole('region', { name: 'Technique Parameters' }))
      .getByRole('textbox', { name: 'prepended_conversation_id' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Parameters' }))
      .queryByRole('textbox', { name: 'objective' })).not.toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('should block preview while edits are invalid, awaiting validation, or rejected by the server', async () => {
    const user = userEvent.setup()
    let resolveEdit: (result: Awaited<ReturnType<typeof techniquesApi.validateEdit>>) => void = () => {
      throw new Error('Validation has not started')
    }
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    mockTechniques.validateEdit.mockImplementationOnce(() => new Promise((resolve) => { resolveEdit = resolve }))
    renderDetail('/scanner/technique.execute')
    const turns = await screen.findByRole('spinbutton', { name: 'Turn count' })
    await user.clear(turns)
    await user.type(turns, '0')
    expect(screen.getByRole('alert')).toHaveTextContent('Turn count must be a positive integer.')
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    expect(mockTechniques.validateEdit).not.toHaveBeenCalled()
    await user.clear(turns)
    await user.type(turns, '3')
    await waitFor(() => expect(mockTechniques.validateEdit).toHaveBeenCalledTimes(1))
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    const configuration = techniqueConfiguration('default_technique')
    await act(async () => {
      resolveEdit({
        configuration, override: {
          source_name: configuration.source_name, source_identity: configuration.source_identity,
          changes: { simulation: { num_turns: 3 } }, dependencies: {}, effective_identity: 'edited',
        },
      })
    })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    mockTechniques.validateEdit.mockRejectedValueOnce(new Error('Cannot validate this draft'))
    await user.clear(turns)
    await user.type(turns, '4')
    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot validate this draft')
    expect(turns).toHaveValue(4)
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    expect(mockStartRun).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Reset to original' }))
    expect(await screen.findByRole('spinbutton', { name: 'Turn count' })).toHaveValue(2)
    await waitFor(() => expect(screen.getByRole('button', { name: 'Launch scan' })).toBeEnabled())
  })

  it('should hide unsupported history controls without silently discarding an existing conversation', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      max_concrete_techniques: 1,
      supported_parameters: [
        { name: 'prepended_conversation_id', type_name: 'str', required: false, default: 'saved-history' },
        { name: 'apply_converters_to_prepended_history', type_name: 'bool', required: false, default: 'False' },
      ],
    }))
    mockTechniques.getConfiguration.mockImplementation(async (name: string) => ({
      ...techniqueConfiguration(name), supports_prepended_conversation: false,
    }))
    renderDetail('/scanner/technique.execute')
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    expect(screen.queryByRole('textbox', { name: 'prepended_conversation_id' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add new conversation' })).not.toBeInTheDocument()
    expect(screen.getByText('This technique does not accept a prepended conversation.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Clear the saved conversation before launch')
    await user.click(screen.getByRole('button', { name: 'Clear saved conversation' }))
    const preview = await openRunPreview(user)
    await waitFor(() => expect(within(preview).getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    await user.click(within(preview).getByRole('button', { name: 'Launch scan' }))
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0].scenario_params ?? {}).not.toHaveProperty('prepended_conversation_id')
    expect(mockStartRun.mock.calls[0][0].scenario_params ?? {}).not.toHaveProperty('apply_converters_to_prepended_history')
  })

  it('allows keyboard changes to a single technique without changing baseline', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({ max_concrete_techniques: 1 }))
    renderDetail('/scanner/foundry.red_team_agent')
    await user.click(await screen.findByRole('radio', { name: 'default_technique' }))
    await user.keyboard('{ArrowRight}')
    expect(screen.getByRole('radio', { name: 'crescendo' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'default_technique' })).not.toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'baseline' })).toBeChecked()
  })

  it('blocks estimates and launch for an over-limit selection until the user selects one technique', async () => {
    jest.useFakeTimers()
    const timedUser = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    mockGetScenario.mockResolvedValue(makeScenario({
      max_concrete_techniques: 1,
      default_techniques: ['default_technique', 'crescendo'],
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByRole('radio', { name: 'crescendo' })
    await advanceTimers(300)
    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Launch scan' })).toBeDisabled()
    expect(screen.getAllByRole('alert').some(
      (alert) => alert.textContent === 'Select at most 1 concrete technique(s).',
    )).toBe(true)
    expect(mockStartRun).not.toHaveBeenCalled()
    await timedUser.click(screen.getByRole('radio', { name: 'default_technique' }))
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    await advanceTimers(300)
    expect(mockEstimateRun).toHaveBeenCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({ techniques: ['default_technique'] }),
      expect.any(AbortSignal),
    )
  })

  it('enforces larger limits on both individual and tag selections without counting baseline', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      max_concrete_techniques: 2,
      all_techniques: ['default_technique', 'crescendo', 'many_shot'],
      technique_summaries: [
        { name: 'default_technique', tags: ['single_turn'] },
        { name: 'crescendo', tags: ['multi_turn'] },
        { name: 'many_shot', tags: ['multi_turn'] },
      ],
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByRole('checkbox', { name: 'crescendo' })
    expect(screen.getAllByRole('button', { name: 'Select Multi-turn techniques' })[0]).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'crescendo' }))
    expect(screen.getByRole('checkbox', { name: 'many_shot' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'baseline' })).toBeEnabled()
    await user.click(screen.getByRole('checkbox', { name: 'default_technique' }))
    await user.click(screen.getAllByRole('button', { name: 'Select Multi-turn techniques' })[0])
    expect(screen.getByRole('checkbox', { name: 'crescendo' })).toBeChecked()
    expect(screen.getByRole('checkbox', { name: 'many_shot' })).toBeChecked()
    await confirmRunPreview(user)
    expect(mockStartRun.mock.calls[0][0].techniques).toEqual(['crescendo', 'many_shot'])
    expect(mockStartRun.mock.calls[0][0].include_baseline).toBe(true)
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

  it('preserves an unknown default limit when blank, with default concurrency/retries', async () => {
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
          dataset_size: { kind: 'bounded', value: 8 }, dataset_limit: { state: 'value', value: 8 },
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
    expect(screen.getByRole('button', { name: 'Edit Maximum objectives' })).toHaveTextContent('Up to 8')
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

  it('uses the configured default and labels the pre-run total as approximate', async () => {
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: {
        ...makeEstimate(10),
        status: 'approximate',
        dataset_size: { kind: 'bounded', value: 10 }, dataset_limit: { state: 'value', value: 5 },
      },
    }))
    mockEstimateRun.mockResolvedValue({
      ...makeEstimate(10),
      status: 'approximate',
      dataset_size: { kind: 'bounded', value: 10 }, dataset_limit: { state: 'value', value: 10 },
    })

    renderDetail('/scanner/foundry.red_team_agent')

    expect(await screen.findByRole('button', { name: 'Edit Maximum objectives' })).toHaveTextContent('Up to 5')
    expect(await screen.findByText('Up to 10')).toBeInTheDocument()
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')
  })

  it('does not use a combined population budget as an editable dataset limit', async () => {
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: {
        ...makeEstimate(40),
        dataset_size: { kind: 'bounded', value: 20 },
        dataset_limit: { state: 'scenario_default' },
      },
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    expect(await screen.findByRole('button', { name: 'Edit Maximum objectives' })).toHaveTextContent('All available')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalled())
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')
  })

  it('does not expose a generated prompt budget as a dataset limit', async () => {
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: {
        ...makeEstimate(124),
        dataset_size: { kind: 'bounded', value: 62 },
        dataset_limit: { state: 'not_applicable' },
      },
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByRole('table', { name: 'Selected datasets' })
    expect(screen.queryByRole('textbox', { name: 'Maximum objectives' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Use all data' })).not.toBeInTheDocument()
  })

  it('keeps scenario defaults when the dataset-limit field is cleared', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: {
        ...makeEstimate(10),
        dataset_size: { kind: 'bounded', value: 5 },
        dataset_limit: { state: 'value', value: 5 },
      },
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    await setObjectiveLimit(user, '2')
    await openObjectiveLimit(user)
    await user.click(screen.getByRole('button', { name: 'Reset to 5' }))
    await user.keyboard('{Escape}')
    expect(screen.queryByRole('button', { name: 'Use all data' })).not.toBeInTheDocument()
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalled())
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size')
    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('max_dataset_size')
  })

  it('sends an explicit all total for both estimation and launch', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await setObjectiveLimit(user, '2')
    await openObjectiveLimit(user)
    await user.click(screen.getByRole('checkbox', { name: 'All available for Maximum objectives' }))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1]).toHaveProperty('datasets.max_total', 'all'))
    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).toHaveProperty('datasets.max_total', 'all')
  })

  it('restores the scenario default when no-total-limit is unchecked', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      default_run_size: {
        ...makeEstimate(10),
        dataset_limit: { state: 'value', value: 5 },
      },
    }))
    renderDetail('/scanner/foundry.red_team_agent')
    await openObjectiveLimit(user)
    await user.click(screen.getByRole('checkbox', { name: 'All available for Maximum objectives' }))
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1]).toHaveProperty('datasets.max_total', 'all'))
    await user.click(screen.getByRole('button', { name: 'Reset to 5' }))
    await user.keyboard('{Escape}')
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('max_dataset_size'))
    await confirmRunPreview(user)
    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    expect(mockStartRun.mock.calls[0][0]).not.toHaveProperty('max_dataset_size')
  })

  it('includes dataset overrides and filters when provided', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    const addDataset = screen.getByRole('combobox', { name: 'Add dataset' })
    await user.click(addDataset)
    await user.click(screen.getByRole('option', { name: 'ds_a (unknown)', exact: true }))
    await user.click(addDataset)
    await user.click(screen.getByRole('option', { name: 'ds_b (unknown)', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Remove harmbench' }))
    await setObjectiveLimit(user, '25')
    await user.click(screen.getAllByRole('button', { name: 'Filter harm category other' })[0])
    await user.click(screen.getAllByRole('button', { name: 'Filter data type Audio' })[0])
    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalled())
    const request = mockStartRun.mock.calls[0][0]
    expect(request.datasets.sources).toEqual([{ name: 'ds_a' }, { name: 'ds_b' }])
    expect(request.datasets.max_total).toBe(25)
    expect(request.datasets.filters).toEqual({
      harm_categories: ['cyber', 'violence'],
      data_types: ['text', 'image_path'],
    })
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent',
      expect.objectContaining({
        target_name: 'target-a',
        techniques: ['default_technique'],
        datasets: {
          sources: [{ name: 'ds_a' }, { name: 'ds_b' }],
          max_total: 25,
          filters: {
          harm_categories: ['cyber', 'violence'],
          data_types: ['text', 'image_path'],
          },
        },
        include_baseline: true,
      }),
      expect.any(AbortSignal),
    ))
    expect(mockEstimateRun.mock.calls.at(-1)?.[1]).not.toHaveProperty('labels')
  })

  it('refreshes parameter-aware defaults after invalid source edits without silently dropping them', async () => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValueOnce(makeScenario({
      supported_parameters: [
        { name: 'scope', type_name: 'str', required: false, default: 'all', choices: null, is_list: false },
      ],
    }))
    mockEstimateRun.mockImplementation(async (_name: string, request: ScenarioRunSizeEstimateRequest) => {
      const narrowed = request.scenario_params?.scope === 'one'
      if (narrowed && request.datasets?.sources) {
        throw { isAxiosError: true, response: { status: 400, data: { detail: 'Dataset is outside the selected scope.' } } }
      }
      const name = narrowed ? 'remote' : 'harmbench'
      const settings = {
        sources: [{ name, max_size: 5, fetch: 'never', configured_provider: false }],
        max_per_dataset: 5, max_total: 5, fetch: 'never', filters: {},
      }
      return {
        ...makeEstimate(10), dataset_defaults: settings, effective_datasets: settings,
        dataset_options: {
          mode: 'subset', allowed_names: [name], per_dataset_limit: true, total_limit: true,
          supports_overrides: true, filters: true,
        },
      }
    })
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByRole('table', { name: 'Selected datasets' })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Edit harmbench objective limit' })).toHaveTextContent('Up to 5'))
    await user.click(screen.getByRole('button', { name: 'Edit harmbench objective limit' }))
    const all = screen.getByRole('checkbox', { name: 'All available for harmbench objective limit' })
    if (all instanceof HTMLInputElement && all.checked) await user.click(all)
    await user.clear(screen.getByRole('textbox', { name: 'harmbench objective limit' }))
    await user.type(screen.getByRole('textbox', { name: 'harmbench objective limit' }), '1')
    await user.keyboard('{Escape}')
    await user.clear(screen.getByTestId('scenario-param-scope'))
    await user.type(screen.getByTestId('scenario-param-scope'), 'one')
    await screen.findByText('Select a permitted source: harmbench')
    expect(screen.getByRole('button', { name: 'Edit harmbench objective limit' })).toHaveTextContent('Up to 1')
    expect(mockEstimateRun.mock.calls.some((call) =>
      call[1].scenario_params?.scope === 'one' && call[1].datasets === undefined,
    )).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Reset datasets' }))
    await screen.findByText('remote (unknown)', { exact: true })
  })

  it('rejects a non-positive-integer max dataset size', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await setObjectiveLimit(user, '0')
    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findAllByRole('alert')).toEqual(expect.arrayContaining([
      expect.objectContaining({ textContent: expect.stringContaining('Maximum objectives must be a positive integer.') }),
    ]))
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it.each(['user', 'unknown'])('warns before replacing %s data and confirms through Run preview', async (origin) => {
    const user = userEvent.setup()
    jest.mocked(datasetsApi.listDatasets).mockResolvedValueOnce({ items: [{
      name: 'harmbench', selection_key: 'dataset:named:harmbench', loaded: true, provider_available: true,
      logical_examples: 8, origins: [origin], modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false,
    }] })
    mockEstimateRun.mockResolvedValue({
      ...makeEstimate(8), effective_datasets: {
        sources: [{ name: 'harmbench', max_size: 5, fetch: 'always', configured_provider: false }],
        max_per_dataset: 5, max_total: 'all', fetch: 'always', filters: {},
      },
    })
    renderDetail('/scanner/foundry.red_team_agent')
    await user.click(await screen.findByRole('radio', { name: 'Refresh' }))
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    const preview = await screen.findByRole('dialog', { name: 'Run preview' })
    await waitFor(() => expect(within(preview).getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    expect(within(preview).getByRole('alert')).toHaveTextContent('full stored datasets: harmbench')
    expect(within(preview).getByRole('alert')).toHaveTextContent('User or unknown-origin data will be replaced: harmbench')
    expect(within(preview).getByRole('alert')).toHaveTextContent('prevent older runs from resuming')
    await user.click(within(preview).getByRole('button', { name: 'Cancel' }))
    expect(mockStartRun).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    const confirmed = await screen.findByRole('dialog', { name: 'Run preview' })
    await user.click(within(confirmed).getByRole('button', { name: 'Launch scan' }))
    expect(mockStartRun).toHaveBeenCalledTimes(1)
    expect(mockStartRun.mock.calls[0][0].datasets).toEqual({
      fetch: 'always', sources: [{ name: 'harmbench', fetch: 'always' }],
    })
  })

  it('shows a replacement warning when dataset metadata cannot be checked', async () => {
    const user = userEvent.setup()
    jest.mocked(datasetsApi.listDatasets).mockRejectedValueOnce(new Error('Metadata unavailable'))
    mockEstimateRun.mockResolvedValue({
      ...makeEstimate(8), effective_datasets: {
        sources: [{ name: 'harmbench', max_size: 5, fetch: 'always', configured_provider: false }],
        max_per_dataset: 5, max_total: 'all', fetch: 'always', filters: {},
      },
    })
    renderDetail('/scanner/foundry.red_team_agent')
    await user.click(await screen.findByRole('radio', { name: 'Refresh' }))
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    const preview = await screen.findByRole('dialog', { name: 'Run preview' })
    await waitFor(() => expect(within(preview).getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    expect(within(preview).getByRole('alert')).toHaveTextContent('Existing data could not be checked for: harmbench')
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('uses current backend policies instead of a global refresh draft for the warning', async () => {
    const user = userEvent.setup()
    mockEstimateRun.mockResolvedValue({
      ...makeEstimate(8), effective_datasets: {
        sources: [
          { name: 'harmbench', max_size: 5, fetch: 'never', configured_provider: false },
          { name: 'remote', max_size: 5, fetch: 'always', configured_provider: false },
        ],
        max_per_dataset: 5, max_total: 'all', fetch: 'always', filters: {},
      },
    })
    renderDetail('/scanner/foundry.red_team_agent')
    await user.click(await screen.findByRole('radio', { name: 'Refresh' }))
    const preview = await openRunPreview(user)
    await waitFor(() => expect(within(preview).getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    const warning = within(preview).getByRole('alert')
    expect(warning).toHaveTextContent('full stored datasets: remote.')
    expect(warning).not.toHaveTextContent('harmbench')
  })

  it('waits for the current projection after removing and re-adding a default source', async () => {
    const user = userEvent.setup()
    const defaults: ScenarioDatasetSettings = {
      sources: ['ds_a', 'ds_b'].map((name) => ({ name, max_size: 5, fetch: 'if_missing', configured_provider: false })),
      max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
    }
    mockGetScenario.mockResolvedValue(makeScenario({ default_datasets: ['ds_a', 'ds_b'], dataset_defaults: defaults }))
    let resolveEstimate: (value: ScenarioRunSizeEstimateResponse) => void = () => {}
    mockEstimateRun.mockImplementation(() => new Promise((resolve) => { resolveEstimate = resolve }))
    renderDetail('/scanner/foundry.red_team_agent')
    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(1))
    await act(async () => resolveEstimate({ ...makeEstimate(8), effective_datasets: defaults }))
    await user.click(screen.getByRole('radio', { name: 'Refresh' }))
    await user.click(screen.getByRole('button', { name: 'Remove ds_b' }))
    await user.click(screen.getByRole('combobox', { name: 'Add dataset' }))
    await user.click(screen.getByRole('option', { name: 'ds_b (unknown)', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    const preview = await screen.findByRole('dialog', { name: 'Run preview' })
    const confirm = within(preview).getByRole('button', { name: 'Launch scan' })
    expect(confirm).toBeDisabled()
    expect(within(preview).getByRole('status')).toHaveTextContent('Checking dataset refresh policies')
    await waitFor(() => expect(mockEstimateRun.mock.calls.at(-1)?.[1].datasets?.sources).toEqual([
      { name: 'ds_a', fetch: 'always' }, { name: 'ds_b' },
    ]))
    await act(async () => resolveEstimate({
      ...makeEstimate(8), effective_datasets: {
        ...defaults, fetch: 'always', sources: defaults.sources.map((source) => ({ ...source, fetch: 'always' })),
      },
    }))
    expect(confirm).toBeEnabled()
    expect(within(preview).getByRole('alert')).toHaveTextContent('full stored datasets: ds_a, ds_b.')
    await user.click(confirm)
    expect(mockStartRun).toHaveBeenCalledTimes(1)
    expect(mockStartRun.mock.calls[0][0].datasets).toEqual({
      fetch: 'always', sources: [{ name: 'ds_a', fetch: 'always' }, { name: 'ds_b' }],
    })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument())
  })

  it.each(['always', 'if_missing'] as const)(
    'preserves the visible %s draft and allows recovery when a protected-policy estimate fails', async (policy) => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      dataset_defaults: {
        sources: [{ name: 'harmbench', max_size: 5, fetch: 'if_missing', configured_provider: false }],
        max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {},
      },
      dataset_options: {
        mode: 'fixed', allowed_names: [], supports_overrides: true, source_settings: false,
        per_dataset_limit: false, total_limit: true, filters: true,
      },
    }))
    mockEstimateRun.mockRejectedValue(new Error('Estimate unavailable'))
    renderDetail('/scanner/foundry.red_team_agent')
    await user.click(await screen.findByRole('radio', { name: 'Refresh' }))
    await waitFor(() => expect(mockEstimateRun).toHaveBeenLastCalledWith(
      'foundry.red_team_agent', expect.objectContaining({ datasets: { fetch: 'always' } }), expect.any(AbortSignal),
    ))
    const failedPreview = await openRunPreview(user)
    await waitFor(() => expect(within(failedPreview).getByRole('button', { name: 'Launch scan' })).toBeEnabled())
    expect(within(failedPreview).getByRole('alert')).toHaveTextContent('Cannot confirm which datasets will refresh')
    await user.click(within(failedPreview).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument())
    expect(screen.getByRole('radio', { name: 'Refresh' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Load missing' })).toBeEnabled()
    if (policy === 'if_missing') {
      await user.click(screen.getByRole('radio', { name: 'Load missing' }))
      expect(screen.getByRole('radio', { name: 'Load missing' })).toBeChecked()
    }
    const preview = await openRunPreview(user)
    const confirm = within(preview).getByRole('button', { name: 'Launch scan' })
    await waitFor(() => expect(confirm).toBeEnabled())
    expect(within(preview).getByRole('alert')).toHaveTextContent('Cannot confirm which datasets will refresh')
    expect(within(preview).queryByRole('status')).not.toBeInTheDocument()
    await user.click(confirm)
    expect(mockStartRun.mock.calls[0][0].datasets).toEqual({ fetch: policy })
  })

  it.each([
    ['Max concurrency', '500', 'Max concurrency must be an integer from 1 to 100.'],
    ['Max concurrency', '1.5', 'Max concurrency must be an integer from 1 to 100.'],
    ['Max retries', '21', 'Max retries must be an integer from 0 to 20.'],
    ['Max retries', '1.5', 'Max retries must be an integer from 0 to 20.'],
  ])('validates %s typed as %s before launching', async (label, value, message) => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    const input = await screen.findByRole('spinbutton', { name: label })

    await user.clear(input)
    await user.type(input, value)
    await user.tab()
    await user.click(screen.getByTestId('launch-scenario-btn'))

    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(mockStartRun).not.toHaveBeenCalled()
  })

  it('commits typed stepper values and sends the values shown after mouse and keyboard steps', async () => {
    const user = userEvent.setup()
    renderDetail('/scanner/foundry.red_team_agent')
    const retries = await screen.findByRole('spinbutton', { name: 'Max retries' })
    const concurrency = screen.getByRole('spinbutton', { name: 'Max concurrency' })

    await user.clear(retries)
    await user.type(retries, '3')
    await user.tab()
    await user.clear(concurrency)
    await user.type(concurrency, '12{ArrowUp}')
    await user.tab()
    await user.click(screen.getAllByRole('button', { name: 'Increment value' })[1])
    expect(retries).toHaveValue('4')
    expect(concurrency).toHaveValue('13')
    await confirmRunPreview(user)

    await waitFor(() => expect(mockStartRun).toHaveBeenCalledWith(expect.objectContaining({
      max_retries: 4,
      max_concurrency: 13,
    })))
  })

  it('sends the exact RunScenarioRequest payload and attaches labels automatically', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr-1' })

    renderDetail('/scanner/foundry.red_team_agent', {
      labels: { operator: 'roakey', team: 'red' }, operationId: 'operation-1',
    })
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
      labels: { operator: 'roakey', team: 'red' },
      operation_id: 'operation-1',
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
      dataset_size: { kind: 'bounded', value: 4 },
      dataset_limit: { state: 'scenario_default' },
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

  it('navigates to the scanner-history route with the encoded run id on success', async () => {
    const user = userEvent.setup()
    mockStartRun.mockResolvedValueOnce({ scenario_result_id: 'sr/1' })

    renderDetail('/scanner/foundry.red_team_agent')
    await screen.findByTestId('scenario-target-select')

    await confirmRunPreview(user)

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
    await waitFor(() => expect(button).toBeEnabled())
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

  it('should close a failed preview and preserve entered values with a visible error', async () => {
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
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument())
    expect(screen.getByRole('alert')).toHaveTextContent('boom')
    await waitFor(() => expect(screen.getByTestId('launch-scenario-btn')).toHaveFocus())
    expect(screen.getByTestId('scenario-target-select')).toHaveValue('target-b')
    expect(screen.getByTestId('technique-crescendo')).toBeChecked()
    expect(screen.getByTestId('technique-default_technique')).not.toBeChecked()
    expect(screen.getByTestId('scenario-param-attempts')).toHaveValue(3)

    const preview = await openRunPreview(user)
    expect(within(preview).getByText('target-b')).toBeInTheDocument()
    expect(within(preview).getByText('crescendo')).toBeInTheDocument()
    expect(within(preview).getByText('harmbench')).toBeInTheDocument()
    expect(within(preview).getByText('attempts').parentElement).toHaveTextContent('attempts3')
  })

  it.each(['', ' \n\t'])('should reject a blank required objective before preview or launch (%j)', async (value: string) => {
    const user = userEvent.setup()
    mockGetScenario.mockResolvedValue(makeScenario({
      max_concrete_techniques: 1,
      supported_parameters: [{ name: 'objective', type_name: 'str', required: true, multiline: true }],
    }))
    renderDetail('/scanner/technique.execute')
    const objective = await screen.findByRole('textbox', { name: 'objective' })
    expect(objective).toBeRequired()
    expect(screen.getByText('objective', { selector: 'label' })).toHaveTextContent('*')
    if (value) await user.click(objective)
    if (value) await user.paste(value)
    await screen.findByRole('spinbutton', { name: 'Turn count' })
    await user.click(screen.getByRole('button', { name: 'Launch scan' }))
    expect(screen.getByRole('alert')).toHaveTextContent('objective is required.')
    expect(screen.queryByRole('dialog', { name: 'Run preview' })).not.toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
    expect(mockEstimateRun).not.toHaveBeenCalled()
    await user.clear(objective)
    await user.type(objective, 'Write a movie script.')
    const preview = await openRunPreview(user)
    expect(within(preview).getByText('Write a movie script.')).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
  })
  describe('launch gating while generation defaults load', () => {
    it('disables Launch scan while defaultsReady is false', async () => {
      renderDetail('/scanner/foundry.red_team_agent', { defaultsReady: false })
      const launch = await screen.findByRole('button', { name: 'Launch scan' })
      expect(launch).toBeDisabled()
    })

    it('enables Launch scan once defaults are ready', async () => {
      renderDetail('/scanner/foundry.red_team_agent', { defaultsReady: true })
      const launch = await screen.findByRole('button', { name: 'Launch scan' })
      await waitFor(() => expect(launch).toBeEnabled())
    })

    it('gates an open preview and launches with refreshed labels without losing configuration', async () => {
      const user = userEvent.setup()
      const view = renderDetail('/scanner/foundry.red_team_agent')
      await user.selectOptions(await screen.findByRole('combobox', { name: 'Objective Target' }), 'target-b')
      await user.click(screen.getByRole('button', { name: 'Launch scan' }))
      const preview = await screen.findByRole('dialog', { name: 'Run preview' })
      const confirm = within(preview).getByRole('button', { name: 'Launch scan' })

      view.updateDefaults({ defaultsReady: false })
      expect(confirm).toBeDisabled()
      await user.click(confirm)
      expect(mockStartRun).not.toHaveBeenCalled()
      view.updateDefaults({ defaultsReady: true, labels: { operator: 'roakey', operation: 'config_op_v2' } })
      await waitFor(() => expect(confirm).toBeEnabled())
      await user.click(confirm)
      expect(mockStartRun).toHaveBeenCalledWith(expect.objectContaining({
        target_name: 'target-b',
        labels: { operator: 'roakey', operation: 'config_op_v2' },
      }))
    })
  })
})
