import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioSuitesApi } from '@/services/api'
import { UserPreferencesProvider } from '@/hooks/useUserPreferences'
import { readUserPreferences } from '@/utils/userPreferences'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { ScenarioSuiteResponse, TargetInstance } from '@/types'

import LaunchSuiteDialog from './LaunchSuiteDialog'

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: {
    launch: jest.fn(),
  },
}))

const mockLaunch = scenarioSuitesApi.launch as jest.Mock
const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'
const OPERATION_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
const OBJECTIVE_TARGET = makeTarget({ target_registry_name: 'objective-target' })
const ADVERSARIAL_TARGET: TargetInstance = makeTarget({
  target_registry_name: 'adversarial-target',
  capabilities: {
    supports_multi_turn: true,
    supports_json_schema: false,
    supports_json_output: false,
    supports_system_prompt: true,
    supported_input_modalities: ['text'],
    supported_output_modalities: ['text'],
  },
})

function makeSuite({
  origin = 'stored',
  version = 'suite-v1',
  presetOrigin = 'stored',
  presetVersion = 'preset-v1',
}: {
  origin?: 'stored' | 'registered'
  version?: string | null
  presetOrigin?: 'stored' | 'registered'
  presetVersion?: string | null
} = {}): ScenarioSuiteResponse {
  return {
    suite: {
      name: 'nightly_suite',
      schema_version: 1,
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'first_preset' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'second_preset' },
      ],
    },
    origin,
    version,
    entry_statuses: [
      {
        entry_id: FIRST_ENTRY_ID,
        preset_name: 'first_preset',
        origin: presetOrigin,
        preset_version: presetVersion,
        issues: [],
      },
      {
        entry_id: SECOND_ENTRY_ID,
        preset_name: 'second_preset',
        origin: presetOrigin,
        preset_version: presetVersion,
        issues: [],
      },
    ],
  }
}

interface RenderOptions {
  targets?: TargetInstance[]
  suite?: ScenarioSuiteResponse
  defaultObjectiveTarget?: TargetInstance | null
  defaultAdversarialTarget?: TargetInstance | null
  labels?: Record<string, string>
  operationId?: string | null
  defaultsReady?: boolean
}

const onDismiss = jest.fn()
const onSuiteChanged = jest.fn()

function renderDialog({
  suite = makeSuite(),
  defaultObjectiveTarget = OBJECTIVE_TARGET,
  defaultAdversarialTarget = null,
  labels = {},
  operationId = null,
  defaultsReady = true,
  targets = [OBJECTIVE_TARGET, ADVERSARIAL_TARGET],
}: RenderOptions = {}) {
  return render(
    <FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local">
      <MemoryRouter>
        <LaunchSuiteDialog
          suite={suite}
          targets={targets}
          defaultObjectiveTarget={defaultObjectiveTarget}
          defaultAdversarialTarget={defaultAdversarialTarget}
          labels={labels}
          operationId={operationId}
          defaultsReady={defaultsReady}
          onDismiss={onDismiss}
          onSuiteChanged={onSuiteChanged}
        />
      </MemoryRouter>
    </UserPreferencesProvider></FluentProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  window.localStorage.clear()
  mockLaunch.mockResolvedValue({ suite_execution_id: 'execution-1' })
})

describe('LaunchSuiteDialog', () => {
  function dialog(options: RenderOptions) {
    return <FluentProvider theme={webLightTheme}><UserPreferencesProvider accountKey="local"><MemoryRouter>
      <LaunchSuiteDialog suite={makeSuite()} targets={options.targets ?? [OBJECTIVE_TARGET, ADVERSARIAL_TARGET]}
        defaultObjectiveTarget={options.defaultObjectiveTarget ?? null}
        defaultAdversarialTarget={options.defaultAdversarialTarget ?? null}
        labels={{}} operationId={null} defaultsReady={options.defaultsReady ?? true}
        onDismiss={onDismiss} onSuiteChanged={onSuiteChanged} />
    </MemoryRouter></UserPreferencesProvider></FluentProvider>
  }

  it('hydrates untouched defaults when the registry arrives after opening', async () => {
    const user = userEvent.setup()
    const rendered = render(dialog({ targets: [], defaultsReady: false }))
    expect(screen.getByRole('button', { name: 'Launch suite' })).toBeDisabled()
    rendered.rerender(dialog({
      defaultObjectiveTarget: OBJECTIVE_TARGET, defaultAdversarialTarget: ADVERSARIAL_TARGET,
    }))
    expect(screen.getByRole('combobox', { name: 'Target' })).toHaveValue('objective-target')
    expect(screen.getByRole('combobox', { name: 'Adversarial Target' })).toHaveValue('adversarial-target')
    await user.click(screen.getByRole('button', { name: 'Launch suite' }))
    expect(mockLaunch).toHaveBeenCalledWith('nightly_suite', expect.objectContaining({
      target_name: 'objective-target', adversarial_target_name: 'adversarial-target',
    }))
  })

  it('preserves changed targets and an explicit server-default choice on late hydration', async () => {
    const user = userEvent.setup()
    const rendered = render(dialog({ defaultAdversarialTarget: ADVERSARIAL_TARGET }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), 'adversarial-target')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Adversarial Target' }), '')
    rendered.rerender(dialog({
      defaultObjectiveTarget: OBJECTIVE_TARGET, defaultAdversarialTarget: ADVERSARIAL_TARGET,
    }))
    expect(screen.getByRole('combobox', { name: 'Target' })).toHaveValue('adversarial-target')
    expect(screen.getByRole('combobox', { name: 'Adversarial Target' })).toHaveValue('')
    await user.click(screen.getByRole('button', { name: 'Launch suite' }))
    expect(mockLaunch).toHaveBeenCalledWith('nightly_suite', expect.objectContaining({ target_name: 'adversarial-target' }))
    expect(mockLaunch.mock.calls[0][1]).not.toHaveProperty('adversarial_target_name')
  })

  it('does not replace an explicitly cleared objective or submit a removed target', async () => {
    const user = userEvent.setup()
    const rendered = render(dialog({ defaultObjectiveTarget: OBJECTIVE_TARGET }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), '')
    rendered.rerender(dialog({ defaultObjectiveTarget: ADVERSARIAL_TARGET }))
    expect(screen.getByRole('button', { name: 'Launch suite' })).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), 'objective-target')
    rendered.rerender(dialog({ targets: [ADVERSARIAL_TARGET], defaultObjectiveTarget: ADVERSARIAL_TARGET }))
    expect(screen.getByRole('combobox', { name: 'Target' })).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Launch suite' })).toBeDisabled()
    expect(mockLaunch).not.toHaveBeenCalled()
  })

  it('shows every entry in declared order', () => {
    renderDialog()

    const entries = within(screen.getByTestId('launch-suite-entries')).getAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent('first_preset')
    expect(entries[1]).toHaveTextContent('second_preset')
  })

  it('launches the reviewed suite and navigates to grouped progress', async () => {
    const user = userEvent.setup()
    renderDialog({ labels: { team: 'purple' }, operationId: OPERATION_ID })
    expect(readUserPreferences('local').lastUsedTargets).toBeUndefined()

    await user.click(screen.getByTestId('confirm-launch-suite'))

    await waitFor(() => expect(mockLaunch).toHaveBeenCalledWith('nightly_suite', {
      expected_version: 'suite-v1',
      entries: [
        {
          entry_id: FIRST_ENTRY_ID,
          preset_name: 'first_preset',
          origin: 'stored',
          preset_version: 'preset-v1',
        },
        {
          entry_id: SECOND_ENTRY_ID,
          preset_name: 'second_preset',
          origin: 'stored',
          preset_version: 'preset-v1',
        },
      ],
      target_name: 'objective-target',
      max_concurrency: 10,
      max_retries: 0,
      operation_id: OPERATION_ID,
      labels: { team: 'purple' },
    }))
    expect(mockNavigate).toHaveBeenCalledWith(
      '/scanner/history/suites/executions/execution-1',
    )
    expect(readUserPreferences('local').lastUsedTargets?.objective?.registryName).toBe('objective-target')
  })

  it('launches code-defined suites and presets with null versions', async () => {
    const user = userEvent.setup()
    renderDialog({
      suite: makeSuite({
        origin: 'registered',
        version: null,
        presetOrigin: 'registered',
        presetVersion: null,
      }),
    })

    await user.click(screen.getByTestId('confirm-launch-suite'))

    await waitFor(() => expect(mockLaunch).toHaveBeenCalledWith(
      'nightly_suite',
      expect.objectContaining({
        expected_version: null,
        entries: expect.arrayContaining([
          expect.objectContaining({ origin: 'registered', preset_version: null }),
        ]),
      }),
    ))
    expect(screen.getAllByText('Defined in code')).toHaveLength(2)
  })

  it('sends selected targets, concurrency, and retry limits', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), 'adversarial-target')
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'Adversarial Target' }),
      'adversarial-target',
    )
    const concurrency = screen.getByTestId('suite-max-concurrency-input')
    await user.clear(concurrency)
    await user.type(concurrency, '4')
    const retries = screen.getByTestId('suite-max-retries-input')
    await user.clear(retries)
    await user.type(retries, '2')
    await user.click(screen.getByTestId('confirm-launch-suite'))

    await waitFor(() => expect(mockLaunch).toHaveBeenCalledWith(
      'nightly_suite',
      expect.objectContaining({
        target_name: 'adversarial-target',
        adversarial_target_name: 'adversarial-target',
        max_concurrency: 4,
        max_retries: 2,
      }),
    ))
  })

  it('blocks launch until the global context and objective target are ready', () => {
    const first = renderDialog({ defaultsReady: false })
    expect(screen.getByTestId('confirm-launch-suite')).toBeDisabled()
    first.unmount()

    renderDialog({ defaultObjectiveTarget: null })
    expect(screen.getByTestId('confirm-launch-suite')).toBeDisabled()
  })

  it('refreshes the library when the reviewed suite or preset changed', async () => {
    const user = userEvent.setup()
    mockLaunch.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'Suite changed.' } },
    })
    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-suite'))

    await waitFor(() => expect(onSuiteChanged).toHaveBeenCalled())
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('reports a launch failure and allows retry', async () => {
    const user = userEvent.setup()
    mockLaunch.mockRejectedValueOnce(new Error('Target is unavailable.'))
    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-suite'))

    expect(await screen.findByText('Target is unavailable.')).toBeInTheDocument()
    expect(screen.getByTestId('confirm-launch-suite')).toBeEnabled()

    await user.click(screen.getByTestId('confirm-launch-suite'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
  })

  it('does not submit twice while launch preflight is in flight', async () => {
    const user = userEvent.setup()
    let release: () => void = () => {}
    mockLaunch.mockReturnValue(new Promise((resolve) => {
      release = () => resolve({ suite_execution_id: 'execution-1' })
    }))
    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-suite'))
    expect(screen.getByTestId('confirm-launch-suite')).toBeDisabled()

    release()
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
    expect(mockLaunch).toHaveBeenCalledTimes(1)
  })
})
