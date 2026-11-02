import type { ReactNode } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioPresetsApi, scenariosApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type {
  PresetIssue,
  ScenarioPreset,
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateResponse,
} from '@/types'

import ScenarioPresetLibrary from './ScenarioPresetLibrary'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: {
    list: jest.fn(),
    remove: jest.fn(),
    update: jest.fn(),
  },
  scenariosApi: {
    estimateRun: jest.fn(),
  },
}))

const mockList = scenarioPresetsApi.list as jest.Mock
const mockRemove = scenarioPresetsApi.remove as jest.Mock
const mockUpdate = scenarioPresetsApi.update as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock
const onPresetDeleted = jest.fn()

const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const NIGHTLY_PRESET: ScenarioPreset = {
  name: 'nightly_probe',
  scenario_name: 'foundry.red_team_agent',
  techniques: ['crescendo'],
}

const DEFINED_IN_CODE_HINT = 'Defined in code, so it cannot be edited or deleted here.'

function makeItem(
  preset: ScenarioPreset = NIGHTLY_PRESET,
  issues: PresetIssue[] = [],
  runSize: ScenarioRunSizeEstimateResponse | null = null,
): ScenarioPresetResponse {
  return { preset, origin: 'stored', version: 'v1', issues, run_size: runSize }
}

function makeRegisteredItem(preset: ScenarioPreset = NIGHTLY_PRESET): ScenarioPresetResponse {
  return { preset, origin: 'registered', version: null, issues: [], run_size: null }
}

function makeSavedItem(showInScanner: boolean, version: string): ScenarioPresetResponse {
  return { ...makeItem({ ...NIGHTLY_PRESET, show_in_scanner: showInScanner }), version }
}

function makeRunSize(attacks: number): ScenarioRunSizeEstimateResponse {
  return {
    status: 'exact',
    dataset_size: { kind: 'bounded', value: attacks },
    dataset_limit: { state: 'value', value: attacks },
    estimated_attack_count: attacks,
    components: [{ label: 'Crescendo', count: attacks, is_baseline: false, note: null }],
    datasets: [],
    note: null,
  }
}

function httpError(status: number, detail: string): unknown {
  return { isAxiosError: true, response: { status, data: { detail } } }
}

function TestWrapper({ children }: { children: ReactNode }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>{children}</MemoryRouter>
    </FluentProvider>
  )
}

function renderLibrary() {
  return render(<ScenarioPresetLibrary onPresetDeleted={onPresetDeleted} />, { wrapper: TestWrapper })
}

beforeEach(() => {
  jest.clearAllMocks()
  for (const mock of [mockList, mockRemove, mockUpdate, mockEstimateRun]) {
    mock.mockReset()
  }
  mockList.mockResolvedValue({ source: 'presets.yaml', items: [] })
  mockEstimateRun.mockResolvedValue(makeRunSize(12))
})

describe('ScenarioPresetLibrary', () => {
  it('shows an empty state and where presets are stored', async () => {
    renderLibrary()

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
    expect(screen.getByText('Stored in presets.yaml')).toBeInTheDocument()
    expect(mockEstimateRun).not.toHaveBeenCalled()
  })

  it('heads its section "Individual presets" and offers no launch action', async () => {
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })

    renderLibrary()

    const row = await screen.findByTestId('preset-row-nightly_probe')
    expect(screen.getByRole('heading', { level: 2, name: 'Individual presets' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Individual presets' })).toContainElement(row)
    expect(screen.queryByRole('button', { name: /launch/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('launch-preset-nightly_probe')).not.toBeInTheDocument()
  })

  it('lists each saved preset with its scenario and technique count', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [
        makeItem(),
        makeItem({ name: 'weekly_probe', scenario_name: 'foundry.encoding', techniques: ['base64', 'rot13'] }),
      ],
    })

    renderLibrary()

    const row = await screen.findByTestId('preset-row-nightly_probe')
    expect(within(row).getByText('foundry.red_team_agent')).toBeInTheDocument()
    expect(within(row).getByText('1 technique')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'nightly_probe' })).toHaveAttribute(
      'href',
      '/registry/scenario-presets/nightly_probe/edit',
    )
    expect(within(screen.getByTestId('preset-row-weekly_probe')).getByText('2 techniques')).toBeInTheDocument()
  })

  it('reports scenario default techniques when the preset pins none', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeItem({ name: 'nightly_probe', scenario_name: 'foundry.red_team_agent' })],
    })

    renderLibrary()

    expect(await screen.findByText('Scenario default techniques')).toBeInTheDocument()
  })

  it('shows the run size the preset itself produces', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeItem()],
    })
    mockEstimateRun.mockResolvedValue(makeRunSize(24))

    renderLibrary()

    const cell = await screen.findByTestId('preset-run-size-nightly_probe')
    await waitFor(() => expect(within(cell).getByText('24 attacks')).toBeInTheDocument())
  })

  it('paints the table before estimates finish without re-reading preset storage', async () => {
    let complete: (value: ScenarioRunSizeEstimateResponse) => void = () => {}
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeItem()],
    })
    mockEstimateRun.mockReturnValueOnce(new Promise<ScenarioRunSizeEstimateResponse>((resolve) => {
      complete = resolve
    }))

    renderLibrary()

    await screen.findByTestId('preset-row-nightly_probe')
    expect(screen.getByText('Calculating...')).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(1)
    expect(mockList.mock.calls[0][1]).toBe(false)

    await act(async () => { complete(makeRunSize(24)) })

    const cell = screen.getByTestId('preset-run-size-nightly_probe')
    expect(await within(cell).findByText('24 attacks')).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('leaves the run size blank for a preset this deployment cannot size', async () => {
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockEstimateRun.mockRejectedValueOnce(new Error('Size unavailable.'))

    renderLibrary()

    const cell = await screen.findByTestId('preset-run-size-nightly_probe')
    await waitFor(() => expect(within(cell).getByText('—')).toBeInTheDocument())
  })

  it('keeps the table usable when run sizes cannot be calculated', async () => {
    mockList.mockResolvedValueOnce({ source: 'presets.yaml', items: [makeItem()] })
    mockEstimateRun.mockRejectedValueOnce(new Error('sizing unavailable'))

    renderLibrary()

    expect(await screen.findByTestId('preset-row-nightly_probe')).toBeInTheDocument()
    expect(
      await screen.findByText('Run sizes could not be calculated: nightly_probe: sizing unavailable'),
    ).toBeInTheDocument()
  })

  it('estimates the displayed configuration without re-resolving its stored version', async () => {
    const preset: ScenarioPreset = {
      ...NIGHTLY_PRESET,
      datasets: { sources: [{ name: 'harmbench' }], max_per_dataset: 3, max_total: 12 },
      include_baseline: false,
      scenario_params: { max_turns: 4 },
    }
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem(preset)] })

    renderLibrary()

    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledWith(
      preset.scenario_name,
      {
        techniques: preset.techniques,
        datasets: preset.datasets,
        include_baseline: false,
        scenario_params: { max_turns: 4 },
      },
      expect.any(AbortSignal),
    ))
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('keeps successful estimates and joins every failure into one warning', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [
        makeItem(),
        makeItem({ name: 'broken_probe', scenario_name: 'foundry.encoding' }),
        makeItem({ name: 'stale_probe', scenario_name: 'foundry.encoding' }),
      ],
    })
    mockEstimateRun
      .mockResolvedValueOnce(makeRunSize(7))
      .mockRejectedValueOnce(new Error('Invalid configuration.'))
      .mockRejectedValueOnce(httpError(422, 'Unknown technique.'))

    renderLibrary()

    const good = await screen.findByTestId('preset-run-size-nightly_probe')
    expect(await within(good).findByText('7 attacks')).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-run-size-broken_probe')).getByText('—')).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-run-size-stale_probe')).getByText('—')).toBeInTheDocument()
    expect(screen.getByText(
      'Run sizes could not be calculated: broken_probe: Invalid configuration.; stale_probe: Unknown technique.',
    )).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('does not replace a refreshed definition with an older pending estimate', async () => {
    const user = userEvent.setup()
    let complete: (value: ScenarioRunSizeEstimateResponse) => void = () => {}
    const updated = { ...NIGHTLY_PRESET, description: 'Updated configuration.' }
    mockList
      .mockResolvedValueOnce({ source: 'presets.yaml', items: [makeItem()] })
      .mockResolvedValueOnce({
        source: 'presets.yaml',
        items: [{ ...makeItem(updated), version: 'v2' }],
      })
    mockEstimateRun
      .mockReturnValueOnce(new Promise<ScenarioRunSizeEstimateResponse>((resolve) => { complete = resolve }))
      .mockResolvedValue(makeRunSize(24))

    renderLibrary()

    await waitFor(() => expect(mockEstimateRun).toHaveBeenCalledTimes(1))
    const signal: AbortSignal = mockEstimateRun.mock.calls[0][2]
    await user.click(screen.getByRole('button', { name: 'Refresh presets' }))
    const cell = await screen.findByTestId('preset-run-size-nightly_probe')
    expect(await within(cell).findByText('24 attacks')).toBeInTheDocument()
    expect(signal.aborted).toBe(true)

    await act(async () => { complete(makeRunSize(12)) })

    expect(screen.getByText('Updated configuration.')).toBeInTheDocument()
    expect(within(cell).getByText('24 attacks')).toBeInTheDocument()
    expect(within(cell).queryByText('12 attacks')).not.toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('attributes a preset to its author and leaves the column blank without one', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [
        makeItem({ ...NIGHTLY_PRESET, author: 'Ada Lovelace' }),
        makeItem({ name: 'weekly_probe', scenario_name: 'foundry.encoding' }),
      ],
    })

    renderLibrary()

    const authored = await screen.findByTestId('preset-author-nightly_probe')
    expect(within(authored).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(screen.getByTestId('preset-author-weekly_probe')).getByText('—')).toBeInTheDocument()
  })

  it('flags an unresolvable preset, names the missing reference, and does not size it', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeItem(NIGHTLY_PRESET, [
        { field: 'scenario_name', message: 'Unknown scenario "foundry.retired".' },
      ])],
    })

    renderLibrary()

    const issues = await screen.findByTestId('preset-issues-nightly_probe')
    expect(within(issues).getByText('Unknown scenario "foundry.retired".')).toBeInTheDocument()
    expect(screen.getByText('Not runnable here')).toBeInTheDocument()
    const cell = screen.getByTestId('preset-run-size-nightly_probe')
    await waitFor(() => expect(within(cell).getByText('—')).toBeInTheDocument())
    expect(mockEstimateRun).not.toHaveBeenCalled()
    expect(screen.queryByText(/Run sizes could not be calculated/)).not.toBeInTheDocument()
  })

  it('shows no issue list when the server reports no issues', async () => {
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })

    renderLibrary()

    expect(await screen.findByTestId('preset-row-nightly_probe')).toBeInTheDocument()
    expect(screen.queryByTestId('preset-issues-nightly_probe')).not.toBeInTheDocument()
    expect(screen.queryByText('Not runnable here')).not.toBeInTheDocument()
  })

  it('shows whether the scanner lists each preset, listing presets by default', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [
        makeItem(),
        makeItem({ ...NIGHTLY_PRESET, name: 'listed_probe', show_in_scanner: true }),
        makeItem({ ...NIGHTLY_PRESET, name: 'hidden_probe', show_in_scanner: false }),
      ],
    })

    renderLibrary()

    const unset = await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })
    expect(unset).toBeChecked()
    expect(unset).toBeEnabled()
    expect(unset).not.toHaveAttribute('title')
    expect(screen.getByRole('switch', { name: 'Add listed_probe to scanner' })).toBeChecked()
    expect(screen.getByRole('switch', { name: 'Add hidden_probe to scanner' })).not.toBeChecked()
    expect(screen.getByRole('columnheader', { name: /Add to scanner/ })).toBeInTheDocument()
  })

  it('saves a scanner change against the loaded version and keeps the run size', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockUpdate
      .mockResolvedValueOnce(makeSavedItem(false, 'v2'))
      .mockResolvedValueOnce(makeSavedItem(true, 'v3'))

    renderLibrary()

    const cell = await screen.findByTestId('preset-run-size-nightly_probe')
    expect(await within(cell).findByText('12 attacks')).toBeInTheDocument()
    const toggle = screen.getByRole('switch', { name: 'Add nightly_probe to scanner' })
    await user.click(toggle)

    await waitFor(() => expect(toggle).not.toBeChecked())
    expect(mockUpdate).toHaveBeenCalledWith(
      'nightly_probe',
      { ...NIGHTLY_PRESET, show_in_scanner: false },
      'v1',
    )
    expect(within(cell).getByText('12 attacks')).toBeInTheDocument()
    expect(mockEstimateRun).toHaveBeenCalledTimes(1)
    expect(mockList).toHaveBeenCalledTimes(1)

    await user.click(toggle)

    await waitFor(() => expect(toggle).toBeChecked())
    expect(mockUpdate).toHaveBeenLastCalledWith(
      'nightly_probe',
      { ...NIGHTLY_PRESET, show_in_scanner: true },
      'v2',
    )
    expect(within(cell).getByText('12 attacks')).toBeInTheDocument()
  })

  it('locks every scanner switch until a change finishes saving', async () => {
    const user = userEvent.setup()
    const saving = deferred<ScenarioPresetResponse>()
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeItem(), makeItem({ ...NIGHTLY_PRESET, name: 'weekly_probe' })],
    })
    mockUpdate.mockReturnValueOnce(saving.promise)

    renderLibrary()

    const toggle = await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })
    const other = screen.getByRole('switch', { name: 'Add weekly_probe to scanner' })
    await user.click(toggle)

    expect(toggle).toBeDisabled()
    expect(other).toBeDisabled()

    await act(async () => { saving.resolve(makeSavedItem(false, 'v2')) })

    expect(toggle).toBeEnabled()
    expect(toggle).not.toBeChecked()
    expect(other).toBeEnabled()
    expect(other).toBeChecked()
  })

  it('reloads the presets when one changed since it was loaded', async () => {
    const user = userEvent.setup()
    mockList
      .mockResolvedValueOnce({ source: 'presets.yaml', items: [makeItem()] })
      .mockResolvedValueOnce({ source: 'presets.yaml', items: [makeSavedItem(false, 'v2')] })
    mockUpdate.mockRejectedValueOnce(httpError(409, 'Version mismatch.'))

    renderLibrary()

    await user.click(await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' }))

    expect(await screen.findByText(
      "Preset 'nightly_probe' changed since it was loaded. Review it and try again.",
    )).toBeInTheDocument()
    expect(await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })).not.toBeChecked()
    expect(mockList).toHaveBeenCalledTimes(2)
    expect(screen.queryByText('Version mismatch.')).not.toBeInTheDocument()
  })

  it('reports why a scanner change failed and leaves the preset as loaded', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockUpdate.mockRejectedValueOnce(httpError(403, 'Admin access required.'))

    renderLibrary()

    const toggle = await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })
    await user.click(toggle)

    expect(await screen.findByText('Admin access required.')).toBeInTheDocument()
    expect(toggle).toBeChecked()
    expect(toggle).toBeEnabled()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('keeps a saved scanner change when run sizes finish afterwards', async () => {
    const user = userEvent.setup()
    const estimate = deferred<ScenarioRunSizeEstimateResponse>()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockEstimateRun.mockReturnValueOnce(estimate.promise)
    mockUpdate
      .mockResolvedValueOnce(makeSavedItem(false, 'v2'))
      .mockResolvedValueOnce(makeSavedItem(true, 'v3'))

    renderLibrary()

    const toggle = await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })
    expect(screen.getByText('Calculating...')).toBeInTheDocument()
    await user.click(toggle)
    await waitFor(() => expect(toggle).not.toBeChecked())

    await act(async () => { estimate.resolve(makeRunSize(30)) })

    expect(within(screen.getByTestId('preset-run-size-nightly_probe')).getByText('30 attacks')).toBeInTheDocument()
    expect(toggle).not.toBeChecked()

    await user.click(toggle)

    await waitFor(() => expect(toggle).toBeChecked())
    expect(mockUpdate).toHaveBeenLastCalledWith(
      'nightly_probe',
      { ...NIGHTLY_PRESET, show_in_scanner: true },
      'v2',
    )
  })

  it('keeps a preset defined in code read-only, including its scanner listing', async () => {
    mockList.mockResolvedValue({
      source: 'presets.yaml',
      items: [makeRegisteredItem({ ...NIGHTLY_PRESET, show_in_scanner: false })],
    })

    renderLibrary()

    const toggle = await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })
    expect(toggle).toBeDisabled()
    expect(toggle).not.toBeChecked()
    expect(toggle).toHaveAttribute('title', DEFINED_IN_CODE_HINT)
    expect(screen.getByTestId('edit-preset-nightly_probe')).toBeDisabled()
    expect(screen.getByTestId('delete-preset-nightly_probe')).toBeDisabled()
    expect(mockUpdate).not.toHaveBeenCalled()
  })

  it('locks the scanner switch of a stored preset that has no version to save against', async () => {
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [{ ...makeItem(), version: null }] })

    renderLibrary()

    expect(await screen.findByRole('switch', { name: 'Add nightly_probe to scanner' })).toBeDisabled()
    expect(screen.getByTestId('edit-preset-nightly_probe')).toBeEnabled()
  })

  it('surfaces a load failure with a retry that refetches', async () => {
    const user = userEvent.setup()
    mockList.mockRejectedValueOnce(new Error('storage unavailable'))

    renderLibrary()

    expect(await screen.findByTestId('error-state')).toBeInTheDocument()
    expect(screen.getByText('storage unavailable')).toBeInTheDocument()

    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    await user.click(screen.getByTestId('retry-btn'))

    expect(await screen.findByTestId('preset-row-nightly_probe')).toBeInTheDocument()
  })

  it('requires confirmation before deleting, then refreshes and reports the deletion', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockRemove.mockResolvedValue(undefined)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-preset-nightly_probe'))
    expect(mockRemove).not.toHaveBeenCalled()
    expect(onPresetDeleted).not.toHaveBeenCalled()

    mockList.mockResolvedValue({ source: 'presets.yaml', items: [] })
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
    expect(mockRemove).toHaveBeenCalledWith('nightly_probe')
    expect(onPresetDeleted).toHaveBeenCalledTimes(1)
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('holds the confirmation until the server finishes deleting', async () => {
    const user = userEvent.setup()
    const removal = deferred<void>()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockRemove.mockReturnValueOnce(removal.promise)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-preset-nightly_probe'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(screen.getByTestId('confirm-delete-preset')).toHaveTextContent('Deleting...')
    expect(screen.getByTestId('confirm-delete-preset')).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled()
    expect(onPresetDeleted).not.toHaveBeenCalled()

    mockList.mockResolvedValue({ source: 'presets.yaml', items: [] })
    await act(async () => { removal.resolve(undefined) })

    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
    expect(onPresetDeleted).toHaveBeenCalledTimes(1)
  })

  it.each(['Cancel', 'Escape'])('closes the delete confirmation with %s without deleting', async (dismiss: string) => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })

    renderLibrary()

    await user.click(await screen.findByTestId('delete-preset-nightly_probe'))
    const dialog = await screen.findByRole('dialog')
    if (dismiss === 'Escape') await user.keyboard('{Escape}')
    else await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockRemove).not.toHaveBeenCalled()
    expect(onPresetDeleted).not.toHaveBeenCalled()
    expect(screen.getByTestId('preset-row-nightly_probe')).toBeInTheDocument()
  })

  it('reports a delete failure without removing the row or announcing a deletion', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })
    mockRemove.mockRejectedValue(new Error('Admin access required.'))

    renderLibrary()

    await user.click(await screen.findByTestId('delete-preset-nightly_probe'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Admin access required.')).toBeInTheDocument()
    expect(screen.getByTestId('preset-row-nightly_probe')).toBeInTheDocument()
    expect(onPresetDeleted).not.toHaveBeenCalled()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('navigates to the editor for create and edit', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'presets.yaml', items: [makeItem()] })

    renderLibrary()

    await user.click(await screen.findByTestId('edit-preset-nightly_probe'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-presets/nightly_probe/edit')

    await user.click(screen.getByTestId('new-preset-btn'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-presets/new')
  })
})
