import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioPresetsApi, scenariosApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { ScenarioPreset, TargetInstance } from '@/types'

import LaunchPresetDialog from './LaunchPresetDialog'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: {
    resolve: jest.fn(),
  },
  scenariosApi: {
    startRun: jest.fn(),
  },
}))

const mockResolve = scenarioPresetsApi.resolve as jest.Mock
const mockStartRun = scenariosApi.startRun as jest.Mock

const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const PRESET: ScenarioPreset = {
  name: 'nightly_probe',
  scenario_name: 'foundry.red_team_agent',
  techniques: ['crescendo'],
}

const OBJECTIVE_TARGET = makeTarget({ target_registry_name: 'gpt4o' })
const ADVERSARIAL_TARGET: TargetInstance = makeTarget({
  target_registry_name: 'adversary',
  capabilities: {
    supports_multi_turn: true,
    supports_json_schema: false,
    supports_json_output: false,
    supports_system_prompt: true,
    supported_input_modalities: ['text'],
    supported_output_modalities: ['text'],
  },
})

interface RenderOptions {
  preset?: ScenarioPreset
  version?: string
  defaultObjectiveTarget?: TargetInstance | null
  defaultAdversarialTarget?: TargetInstance | null
  labels?: Record<string, string>
}

const onDismiss = jest.fn()
const onPresetChanged = jest.fn()

function renderDialog({
  preset = PRESET,
  version = 'v1',
  defaultObjectiveTarget = OBJECTIVE_TARGET,
  defaultAdversarialTarget = null,
  labels = {},
}: RenderOptions = {}) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        <LaunchPresetDialog
          preset={preset}
          version={version}
          targets={[OBJECTIVE_TARGET, ADVERSARIAL_TARGET]}
          defaultObjectiveTarget={defaultObjectiveTarget}
          defaultAdversarialTarget={defaultAdversarialTarget}
          labels={labels}
          onDismiss={onDismiss}
          onPresetChanged={onPresetChanged}
        />
      </MemoryRouter>
    </FluentProvider>,
  )
}

beforeEach(() => {
  jest.clearAllMocks()
  mockResolve.mockResolvedValue({ scenario_name: 'foundry.red_team_agent', target_name: 'gpt4o' })
  mockStartRun.mockResolvedValue({ scenario_result_id: 'run-1' })
})

describe('LaunchPresetDialog', () => {
  it('reports scenario defaults for the fields the preset leaves unset', () => {
    renderDialog()

    expect(screen.getByTestId('launch-preset-summary')).toHaveTextContent(
      '1 technique · scenario default datasets',
    )
  })

  it('summarizes the configuration the preset pins', () => {
    renderDialog({
      preset: {
        name: 'nightly_probe',
        scenario_name: 'foundry.red_team_agent',
        techniques: ['crescendo', 'flip'],
        dataset_names: ['harmbench', 'advbench'],
        max_dataset_size: 25,
        include_baseline: true,
      },
    })

    expect(screen.getByTestId('launch-preset-summary')).toHaveTextContent(
      '2 techniques · harmbench, advbench · max dataset size 25 · baseline included',
    )
  })

  it('distinguishes an excluded baseline from an included one', () => {
    renderDialog({ preset: { ...PRESET, include_baseline: false } })

    expect(screen.getByTestId('launch-preset-summary')).toHaveTextContent('baseline excluded')
  })

  it('resolves the preset server-side and navigates to the started run', async () => {
    const user = userEvent.setup()
    renderDialog({ labels: { op: 'nightly' } })

    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith('nightly_probe', {
      expected_version: 'v1',
      target_name: 'gpt4o',
      max_concurrency: 10,
      max_retries: 0,
      labels: { op: 'nightly' },
    }))
    expect(mockStartRun).toHaveBeenCalledWith({
      scenario_name: 'foundry.red_team_agent',
      target_name: 'gpt4o',
    })
    expect(mockNavigate).toHaveBeenCalledWith('/scanner-history/run-1', {
      state: { scenarioName: 'foundry.red_team_agent' },
    })
  })

  it('omits the adversarial target and labels when neither is set', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith('nightly_probe', {
      expected_version: 'v1',
      target_name: 'gpt4o',
      max_concurrency: 10,
      max_retries: 0,
    }))
  })

  it('hands a preset edited mid-launch back to the library instead of running it', async () => {
    const user = userEvent.setup()
    mockResolve.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: "Scenario preset 'nightly_probe' changed since it was read" } },
    })

    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(onPresetChanged).toHaveBeenCalled())
    expect(mockStartRun).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('sends the default adversarial target when one is configured', async () => {
    const user = userEvent.setup()
    renderDialog({ defaultAdversarialTarget: ADVERSARIAL_TARGET })

    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ adversarial_target_name: 'adversary' }),
    ))
  })

  it('blocks launching until a target is chosen', () => {
    renderDialog({ defaultObjectiveTarget: null })

    expect(screen.getByTestId('confirm-launch-preset')).toBeDisabled()
  })

  it('ignores a default target that this deployment does not have', () => {
    renderDialog({ defaultObjectiveTarget: makeTarget({ target_registry_name: 'retired' }) })

    expect(screen.getByTestId('confirm-launch-preset')).toBeDisabled()
  })

  it('reports a resolve failure and does not start a run', async () => {
    const user = userEvent.setup()
    mockResolve.mockRejectedValue(new Error('Unknown scenario "foundry.retired".'))

    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-preset'))

    expect(await screen.findByText('Unknown scenario "foundry.retired".')).toBeInTheDocument()
    expect(mockStartRun).not.toHaveBeenCalled()
    expect(mockNavigate).not.toHaveBeenCalled()
  })

  it('re-enables launch after a failure so the operator can retry', async () => {
    const user = userEvent.setup()
    mockStartRun.mockRejectedValueOnce(new Error('Target is unavailable.'))

    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-preset'))

    expect(await screen.findByText('Target is unavailable.')).toBeInTheDocument()
    expect(screen.getByTestId('confirm-launch-preset')).toBeEnabled()

    await user.click(screen.getByTestId('confirm-launch-preset'))
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
  })

  it('dismisses without launching', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(onDismiss).toHaveBeenCalled()
    expect(mockResolve).not.toHaveBeenCalled()
  })

  it('dismisses when the dialog itself is closed', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.keyboard('{Escape}')

    expect(onDismiss).toHaveBeenCalled()
  })

  it('sends the target the operator picked instead of the default', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Target' }), 'adversary')
    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ target_name: 'adversary' }),
    ))
  })

  it('sends the adversarial target the operator picked', async () => {
    const user = userEvent.setup()
    renderDialog()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Adversarial Target' }), 'adversary')
    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ adversarial_target_name: 'adversary' }),
    ))
  })

  it('sends the concurrency and retry limits the operator set', async () => {
    const user = userEvent.setup()
    renderDialog()

    const concurrency = screen.getByTestId('preset-max-concurrency-input')
    await user.clear(concurrency)
    await user.type(concurrency, '4')
    const retries = screen.getByTestId('preset-max-retries-input')
    await user.clear(retries)
    await user.type(retries, '2')

    await user.click(screen.getByTestId('confirm-launch-preset'))

    await waitFor(() => expect(mockResolve).toHaveBeenCalledWith(
      'nightly_probe',
      expect.objectContaining({ max_concurrency: 4, max_retries: 2 }),
    ))
  })

  it('does not resolve twice while a launch is in flight', async () => {
    const user = userEvent.setup()
    let release: () => void = () => {}
    mockResolve.mockReturnValue(new Promise((resolve) => {
      release = () => resolve({ scenario_name: 'foundry.red_team_agent', target_name: 'gpt4o' })
    }))

    renderDialog()

    await user.click(screen.getByTestId('confirm-launch-preset'))
    expect(screen.getByTestId('confirm-launch-preset')).toBeDisabled()

    release()
    await waitFor(() => expect(mockNavigate).toHaveBeenCalled())
    expect(mockResolve).toHaveBeenCalledTimes(1)
  })
})
