import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, useLocation } from 'react-router'

import { scenarioPresetsApi, scenariosApi, scenarioSuitesApi } from '@/services/api'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makePresetResponse, makeRunSize, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { ScenarioSuiteResponse } from '@/types'

import ScannerPresetsPage from './ScannerPresetsPage'

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: { list: jest.fn() },
  scenarioPresetsApi: { list: jest.fn() },
  scenariosApi: { listCatalog: jest.fn(), estimateRun: jest.fn() },
}))

interface LaunchDialogStubProps {
  suite: ScenarioSuiteResponse
  operationId: string | null
  onDismiss: () => void
  onSuiteChanged: () => void
}

jest.mock('@/components/ScenarioSuites/LaunchSuiteDialog', () => ({
  __esModule: true,
  default: ({ suite, operationId, onDismiss, onSuiteChanged }: LaunchDialogStubProps) => (
    <div role="dialog" aria-label={`Launch ${suite.suite.name}`} data-operation-id={operationId ?? 'none'}>
      <button type="button" onClick={onDismiss}>Dismiss launch</button>
      <button type="button" onClick={onSuiteChanged}>Report change</button>
    </div>
  ),
}))

const mockListSuites = scenarioSuitesApi.list as jest.Mock
const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock

const TARGET = makeTarget({ target_registry_name: 'objective-target' })
const PRESETS = [
  makePresetResponse({ name: 'smoke', scenario_name: 'airt.jailbreak' }),
  makePresetResponse({ name: 'encoding_sweep', scenario_name: 'garak.encoding' }),
]
const BROKEN = makeSuite('quarterly_comparison', ['gone'], {
  entryOrigins: [null],
  entryIssues: [[{ field: 'preset_name', message: "Preset 'gone' is not available." }]],
})

function LocationProbe() {
  return <output aria-label="Current route">{useLocation().pathname}</output>
}

function renderPage(defaultsReady = true) {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/scanner/presets']}>
        <ScannerPresetsPage
          targets={[TARGET]}
          defaultObjectiveTarget={TARGET}
          defaultAdversarialTarget={null}
          labels={{}}
          operationId="operation-1"
          defaultsReady={defaultsReady}
        />
        <LocationProbe />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScannerPresetsPage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListSuites.mockResolvedValue({
      source: 'suites',
      items: [
        makeSuite('release_gate', ['smoke'], { description: 'Before every release.' }),
        BROKEN,
        makeSuite('garak_sweep', ['encoding_sweep']),
        makeSuite('empty_set', []),
      ],
    })
    mockListPresets.mockResolvedValue({ source: 'presets', items: PRESETS })
    mockListCatalog.mockResolvedValue({
      items: [makeScenario({ scenario_name: 'airt.jailbreak' }), makeScenario({ scenario_name: 'garak.encoding' })],
      pagination: { limit: 200, has_more: false },
    })
    mockEstimateRun.mockResolvedValue(makeRunSize(90))
  })

  it('lists launchable presets by name and leaves broken ones to the Registry', async () => {
    renderPage()

    expect(screen.getByText('Loading presets...')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { level: 3, name: /^empty_set/ })).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 2, name: 'Presets' })).toBeInTheDocument()
    expect(screen.getByText('Saved sets of scenarios you can launch together.')).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent?.split(' ')[0])).toEqual([
      'empty_set', 'garak_sweep', 'release_gate',
    ])
    expect(screen.queryByText(/quarterly_comparison/)).not.toBeInTheDocument()
  })

  it('searches presets by name, description, and the scenarios they run', async () => {
    const user = userEvent.setup()
    renderPage()
    const search = await screen.findByRole('textbox', { name: 'Search presets' })

    await user.type(search, 'garak.enc')
    expect(screen.getAllByRole('heading', { level: 3 })).toHaveLength(1)
    expect(screen.getByRole('heading', { level: 3, name: /^garak_sweep/ })).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'every release')
    expect(screen.getByRole('heading', { level: 3, name: /^release_gate/ })).toBeInTheDocument()

    await user.clear(search)
    await user.type(search, 'zzz')
    expect(screen.getByTestId('no-results-state')).toHaveTextContent('No presets match "zzz"')
  })

  it('explains why Launch is unavailable for an empty preset or while defaults load', async () => {
    renderPage(false)

    const empty = await screen.findByRole('button', { name: 'Launch empty_set' })
    expect(empty).toHaveAttribute('aria-disabled', 'true')
    expect(empty).toHaveAccessibleDescription('Add a scenario to this preset in the Registry before launching it.')
    expect(screen.getByRole('button', { name: 'Launch release_gate' })).toHaveAccessibleDescription(
      'Launch is available once default labels and the Operation finish loading.',
    )
  })

  it('opens the launch dialog for a preset and reloads with a notice when it changed meanwhile', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Launch release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Launch release_gate' })
    expect(dialog).toHaveAttribute('data-operation-id', 'operation-1')

    await user.click(within(dialog).getByRole('button', { name: 'Report change' }))

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByTestId('suite-changed-notice')).toHaveTextContent(
      "'release_gate' or one of its scenarios changed while you were launching it.",
    )
    expect(mockListSuites).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: 'Dismiss notification' }))
    expect(screen.queryByTestId('suite-changed-notice')).not.toBeInTheDocument()
  })

  it('links to the Registry to manage presets', async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole('button', { name: 'Manage in Registry' }))

    expect(screen.getByRole('status', { name: 'Current route' })).toHaveTextContent('/registry/scenario-presets')
  })

  it('says no preset is ready and points to the Registry when every preset needs attention', async () => {
    const user = userEvent.setup()
    mockListSuites.mockResolvedValue({ source: 'suites', items: [BROKEN] })
    renderPage()

    const state = await screen.findByTestId('presets-attention-state')
    expect(within(state).getByRole('heading', { name: 'No presets are ready to launch' })).toBeInTheDocument()
    expect(screen.queryByText('No presets yet')).not.toBeInTheDocument()
    await user.click(within(state).getByRole('button', { name: 'Open Scenario presets in the Registry' }))

    expect(screen.getByRole('status', { name: 'Current route' })).toHaveTextContent('/registry/scenario-presets')
  })

  it('offers to create the first preset when there are none', async () => {
    const user = userEvent.setup()
    mockListSuites.mockResolvedValue({ source: 'suites', items: [] })
    renderPage()

    const empty = await screen.findByTestId('presets-empty-state')
    expect(within(empty).getByRole('heading', { name: 'No presets yet' })).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Search presets' })).not.toBeInTheDocument()
    await user.click(within(empty).getByRole('button', { name: 'Create a preset' }))

    expect(screen.getByRole('status', { name: 'Current route' })).toHaveTextContent('/registry/scenario-presets/new')
  })

  it('reports a failed load with Retry', async () => {
    const user = userEvent.setup()
    mockListSuites.mockRejectedValueOnce(new Error('Suite storage is unavailable.'))
    renderPage()

    expect(await screen.findByText(/Couldn't load presets\. Suite storage is unavailable\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { level: 3, name: /^release_gate/ })).toBeInTheDocument()
  })
})
