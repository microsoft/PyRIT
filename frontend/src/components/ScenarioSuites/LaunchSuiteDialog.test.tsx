import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioSuitesApi } from '@/services/api'
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
}: RenderOptions = {}) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        <LaunchSuiteDialog
          suite={suite}
          targets={[OBJECTIVE_TARGET, ADVERSARIAL_TARGET]}
          defaultObjectiveTarget={defaultObjectiveTarget}
          defaultAdversarialTarget={defaultAdversarialTarget}
          labels={labels}
          operationId={operationId}
          defaultsReady={defaultsReady}
          onDismiss={onDismiss}
          onSuiteChanged={onSuiteChanged}
        />
      </MemoryRouter>
    </FluentProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockLaunch.mockResolvedValue({ suite_execution_id: 'execution-1' })
})

describe('LaunchSuiteDialog', () => {
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
      '/scanner/suites/executions/execution-1',
    )
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
