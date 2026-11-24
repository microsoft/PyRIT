import type { ReactNode } from 'react'

import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, useLocation } from 'react-router'

import { scenarioPresetsApi, scenarioSuitesApi, scenariosApi } from '@/services/api'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makePresetResponse, makeRunSize, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import type { ScenarioSuite } from '@/types'

import ScenarioPresetRegistry from './ScenarioPresetRegistry'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: { list: jest.fn(), create: jest.fn() },
  scenarioSuitesApi: { list: jest.fn(), get: jest.fn(), create: jest.fn(), update: jest.fn(), remove: jest.fn() },
  scenariosApi: { listCatalog: jest.fn(), estimateRun: jest.fn() },
}))

const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockCreatePreset = scenarioPresetsApi.create as jest.Mock
const mockListSuites = scenarioSuitesApi.list as jest.Mock
const mockGetSuite = scenarioSuitesApi.get as jest.Mock
const mockCreateSuite = scenarioSuitesApi.create as jest.Mock
const mockUpdateSuite = scenarioSuitesApi.update as jest.Mock
const mockRemoveSuite = scenarioSuitesApi.remove as jest.Mock
const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock

const RELEASE = makeSuite('release_gate', ['weekly_smoke', 'leak_check'], { author: 'ateam' })
const QUARTERLY = makeSuite('quarterly_comparison', ['weekly_smoke', 'retired'], {
  description: "Last quarter's configurations.",
  entryOrigins: ['stored', null],
  entryIssues: [[], [{ field: 'preset_name', message: "Preset 'retired' is not available." }]],
})
const BUILTIN = makeSuite('builtin_smoke', ['code_scenario'], { origin: 'registered' })
const PRESETS = [
  makePresetResponse({ name: 'weekly_smoke', scenario_name: 'airt.jailbreak' }),
  makePresetResponse({ name: 'leak_check', scenario_name: 'garak.system_prompt_extraction' }),
  makePresetResponse({ name: 'code_scenario', scenario_name: 'airt.jailbreak' }, { origin: 'registered', version: null }),
]
const SCENARIOS = [
  makeScenario({ scenario_name: 'airt.jailbreak', description: 'Jailbreak templates.' }),
  makeScenario({ scenario_name: 'garak.system_prompt_extraction', description: 'Prompt leakage.' }),
]

function apiError(status: number, detail: string): unknown {
  return { isAxiosError: true, response: { status, data: { detail } } }
}

function LocationProbe() {
  return <output aria-label="Current route">{useLocation().pathname}</output>
}

function TestWrapper({ children, path }: { children: ReactNode; path: string }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[path]}>
        {children}
        <LocationProbe />
      </MemoryRouter>
    </FluentProvider>
  )
}

function renderRegistry({ creating = false } = {}) {
  render(
    <TestWrapper path={creating ? '/registry/scenario-presets/new' : '/registry/scenario-presets'}>
      <ScenarioPresetRegistry />
    </TestWrapper>,
  )
}

function route(): string {
  // A probe outside the page; a closing dialog may still hide it from the accessibility tree.
  return screen.getByRole('status', { name: 'Current route', hidden: true }).textContent ?? ''
}

function card(name: string): HTMLElement {
  return screen.getByTestId(`registry-suite-${name}`)
}

async function openCard(user: ReturnType<typeof userEvent.setup>, name: string): Promise<HTMLElement> {
  const toggle = await screen.findByRole('button', { name: new RegExp(`^${name} `) })
  await user.click(toggle)
  return card(name)
}

async function chooseScenario(user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, name: string) {
  await user.click(within(dialog).getByRole('combobox', { name: 'Scenario' }))
  await user.click(await screen.findByRole('option', { name }))
}

function updatedSuite(call = 0): ScenarioSuite {
  return mockUpdateSuite.mock.calls[call][1] as ScenarioSuite
}

describe('ScenarioPresetRegistry', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListSuites.mockResolvedValue({ source: 'suites', items: [RELEASE, QUARTERLY, BUILTIN] })
    mockListPresets.mockResolvedValue({ source: 'presets', items: PRESETS })
    mockListCatalog.mockResolvedValue({ items: SCENARIOS, pagination: { limit: 200, has_more: false } })
    mockEstimateRun.mockResolvedValue(makeRunSize(90))
    mockCreatePreset.mockImplementation(async (preset) => makePresetResponse(preset))
    mockCreateSuite.mockImplementation(async (suite) => suite)
    mockUpdateSuite.mockResolvedValue(RELEASE)
    mockRemoveSuite.mockResolvedValue(undefined)
  })

  it('lists presets as cards with those needing attention first', async () => {
    renderRegistry()

    expect(screen.getByRole('heading', { level: 1, name: 'Scenario presets' })).toBeInTheDocument()
    expect(screen.getByText('A scenario preset is a saved set of scenarios that the Scanner launches together.'))
      .toBeInTheDocument()
    await screen.findByTestId('registry-suite-release_gate')
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual([
      'quarterly_comparison 2 scenarios',
      'builtin_smoke 1 scenario',
      'release_gate 2 scenarios · ateam',
    ])
    expect(within(card('quarterly_comparison')).getByText('Needs attention')).toBeInTheDocument()
    expect(within(card('builtin_smoke')).getByText('Defined in code')).toBeInTheDocument()
  })

  it('opens the presets that need attention from the banner and says how to fix each scenario', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const banner = await screen.findByTestId('attention-banner')
    expect(banner).toHaveTextContent("1 preset needs attention and won't show in the Scanner until fixed.")
    await user.click(within(banner).getByRole('button', { name: 'Show' }))

    const broken = screen.getByTestId('suite-member-quarterly_comparison-entry-2')
    expect(within(broken).getByText("Preset 'retired' is not available.")).toBeInTheDocument()
    // A missing scenario may only be unreadable for now, so the row adds no advice to remove it.
    expect(within(broken).queryByText(/Remove it|Edit it/)).not.toBeInTheDocument()
    expect(within(broken).queryByRole('button', { name: /^Edit/ })).not.toBeInTheDocument()
    expect(within(broken).getByRole('button', { name: 'Remove retired from quarterly_comparison' })).toBeInTheDocument()
    expect(screen.queryByTestId('suite-member-release_gate-entry-1')).not.toBeInTheDocument()
  })

  it('lists each scenario led by what it runs, with sharing and named row actions', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const release = await openCard(user, 'release_gate')
    const row = within(release).getByTestId('suite-member-release_gate-entry-1')
    expect(within(row).getByText('airt.jailbreak')).toBeInTheDocument()
    expect(within(row).getByText('Saved as weekly_smoke')).toBeInTheDocument()
    expect(within(row).getByText('Jailbreak templates.')).toBeInTheDocument()
    expect(within(row).getByText('Also in 1 other preset')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Edit weekly_smoke' })).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Remove weekly_smoke from release_gate' })).toBeInTheDocument()
    expect(await within(row).findByText('90 attacks')).toBeInTheDocument()
  })

  it('shows a preset defined in code without actions', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const builtin = await openCard(user, 'builtin_smoke')
    expect(within(builtin).queryByRole('button', { name: /Add scenario|Edit|Delete|Remove/ })).not.toBeInTheDocument()
    expect(within(builtin).getByText('airt.jailbreak')).toBeInTheDocument()
  })

  it('creates a preset with its first scenario and opens it', async () => {
    const user = userEvent.setup()
    mockListSuites
      .mockResolvedValueOnce({ source: 'suites', items: [RELEASE] })
      .mockResolvedValue({ source: 'suites', items: [RELEASE, makeSuite('nightly', ['nightly_airt_jailbreak'])] })
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'New preset' }))
    const dialog = screen.getByRole('dialog', { name: 'New preset' })
    expect(within(dialog).getByRole('button', { name: 'Create' })).toBeDisabled()
    await user.type(within(dialog).getByRole('textbox', { name: /Name/ }), 'nightly')
    await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Every night.')
    await chooseScenario(user, dialog, 'airt.jailbreak')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockCreatePreset).toHaveBeenCalledWith({ name: 'nightly_airt_jailbreak', scenario_name: 'airt.jailbreak' })
    expect(mockCreateSuite).toHaveBeenCalledWith(expect.objectContaining({ name: 'nightly', description: 'Every night.' }))
    expect(await screen.findByTestId('suite-member-nightly-entry-1')).toBeInTheDocument()
  })

  it('rejects an invalid name before saving anything', async () => {
    const user = userEvent.setup()
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'New preset' }))
    const dialog = screen.getByRole('dialog', { name: 'New preset' })
    await user.type(within(dialog).getByRole('textbox', { name: /Name/ }), 'release-gate')
    await chooseScenario(user, dialog, 'airt.jailbreak')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    expect(within(dialog).getByText(/Use lowercase letters, digits and underscores/)).toBeInTheDocument()
    expect(mockCreatePreset).not.toHaveBeenCalled()
  })

  it('says the scenario was saved but not added when the preset write fails, then retries', async () => {
    const user = userEvent.setup()
    mockCreateSuite.mockRejectedValueOnce(apiError(409, "Suite 'release_gate' already exists."))
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'New preset' }))
    const dialog = screen.getByRole('dialog', { name: 'New preset' })
    await user.type(within(dialog).getByRole('textbox', { name: /Name/ }), 'release_gate')
    await chooseScenario(user, dialog, 'airt.jailbreak')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    const notice = await within(dialog).findByTestId('partial-save-notice')
    expect(notice).toHaveTextContent('The scenario was saved but not added to a new preset.')
    expect(notice).toHaveTextContent("Suite 'release_gate' already exists.")
    const name = within(dialog).getByRole('textbox', { name: /Name/ })
    await user.clear(name)
    await user.type(name, 'release_gate_two')
    await user.click(within(dialog).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockCreatePreset).toHaveBeenCalledTimes(1)
    expect(mockCreateSuite).toHaveBeenLastCalledWith(expect.objectContaining({
      name: 'release_gate_two',
      entries: [expect.objectContaining({ preset_name: 'release_gate_airt_jailbreak' })],
    }))
  })

  it('opens New preset from its route and returns to the page when dismissed', async () => {
    const user = userEvent.setup()
    renderRegistry({ creating: true })

    const dialog = await screen.findByRole('dialog', { name: 'New preset' })
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    expect(route()).toBe('/registry/scenario-presets')
  })

  it('keeps the created preset open after creating it from the New preset route', async () => {
    const user = userEvent.setup()
    mockListSuites
      .mockResolvedValueOnce({ source: 'suites', items: [RELEASE] })
      .mockResolvedValue({ source: 'suites', items: [RELEASE, makeSuite('nightly', ['nightly_airt_jailbreak'])] })
    renderRegistry({ creating: true })

    const dialog = await screen.findByRole('dialog', { name: 'New preset' })
    await user.type(within(dialog).getByRole('textbox', { name: /Name/ }), 'nightly')
    await chooseScenario(user, dialog, 'airt.jailbreak')
    await user.click(within(dialog).getByRole('button', { name: 'Create' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByTestId('suite-member-nightly-entry-1')).toBeInTheDocument()
    expect(route()).toBe('/registry/scenario-presets')
  })

  it('adds a scenario to a preset', async () => {
    const user = userEvent.setup()
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'Add scenario to release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a scenario to release_gate' })
    await chooseScenario(user, dialog, 'garak.system_prompt_extraction')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockCreatePreset).toHaveBeenCalledWith({
      name: 'release_gate_garak_system_prompt_extraction',
      scenario_name: 'garak.system_prompt_extraction',
    })
    expect(mockUpdateSuite).toHaveBeenCalledWith('release_gate', expect.anything(), 'release_gate-v1')
    expect(updatedSuite().entries.map((entry) => entry.preset_name)).toEqual([
      'weekly_smoke', 'leak_check', 'release_gate_garak_system_prompt_extraction',
    ])
  })

  it('keeps Add scenario open with Retry when the second write fails', async () => {
    const user = userEvent.setup()
    mockUpdateSuite.mockRejectedValueOnce(apiError(409, 'Version mismatch.'))
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'Add scenario to release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Add a scenario to release_gate' })
    await chooseScenario(user, dialog, 'airt.jailbreak')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    expect(await within(dialog).findByTestId('partial-save-notice')).toHaveTextContent(
      'The scenario was saved but not added to release_gate.',
    )
    const reloaded = { ...makeSuite('release_gate', ['weekly_smoke', 'leak_check', 'added_elsewhere']), version: 'release_gate-v2' }
    mockGetSuite.mockResolvedValueOnce(reloaded)
    await user.click(within(dialog).getByRole('button', { name: 'Retry' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockCreatePreset).toHaveBeenCalledTimes(1)
    expect(mockUpdateSuite).toHaveBeenCalledTimes(2)
    expect(mockUpdateSuite).toHaveBeenLastCalledWith('release_gate', expect.anything(), 'release_gate-v2')
    expect(updatedSuite(1).entries.map((entry) => entry.preset_name)).toEqual([
      'weekly_smoke', 'leak_check', 'added_elsewhere', 'release_gate_airt_jailbreak',
    ])
  })

  it("saves a description again with the version reloaded after a conflict", async () => {
    const user = userEvent.setup()
    const reloaded = { ...RELEASE, version: 'release_gate-v2' }
    mockListSuites
      .mockResolvedValueOnce({ source: 'suites', items: [RELEASE] })
      .mockResolvedValue({ source: 'suites', items: [reloaded] })
    mockUpdateSuite.mockRejectedValueOnce(apiError(409, 'Version mismatch.'))
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'Edit release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit release_gate' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Before a model ships.')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await within(dialog).findByText("'release_gate' changed since it was loaded. Review it and try again."))
      .toBeInTheDocument()
    await waitFor(() => expect(mockListSuites).toHaveBeenCalledTimes(2))
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockUpdateSuite).toHaveBeenLastCalledWith('release_gate', expect.anything(), 'release_gate-v2')
  })

  it.each([
    ['techniques', 'Edit it to fix this, or remove it from this preset.'],
    ['scenario_name', 'Remove it from this preset.'],
  ])('says how to fix a %s issue on a saved scenario', async (field, hint) => {
    const user = userEvent.setup()
    const broken = makeSuite('release_gate', ['weekly_smoke'], {
      entryIssues: [[{ field, message: 'The server explains the problem.' }]],
    })
    mockListSuites.mockResolvedValue({ source: 'suites', items: [broken] })
    renderRegistry()

    const release = await openCard(user, 'release_gate')
    const row = within(release).getByTestId('suite-member-release_gate-entry-1')
    expect(within(row).getByText('The server explains the problem.')).toBeInTheDocument()
    expect(within(row).getByText(hint)).toBeInTheDocument()
  })

  it("edits a preset's description", async () => {
    const user = userEvent.setup()
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'Edit release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit release_gate' })
    await user.type(within(dialog).getByRole('textbox', { name: 'Description' }), 'Before a model ships.')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(updatedSuite().description).toBe('Before a model ships.')
  })

  it('deletes a preset after confirmation and reports a conflict', async () => {
    const user = userEvent.setup()
    mockRemoveSuite.mockRejectedValueOnce(apiError(409, 'Version mismatch.'))
    renderRegistry()

    await user.click(await screen.findByRole('button', { name: 'Delete release_gate' }))
    const dialog = screen.getByRole('dialog', { name: 'Delete release_gate?' })
    expect(dialog).toHaveTextContent('Its scenarios stay saved')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(mockRemoveSuite).toHaveBeenCalledWith('release_gate', 'release_gate-v1')
    expect(await screen.findByText("'release_gate' changed since it was loaded. Review it and try again."))
      .toBeInTheDocument()
  })

  it('removes a scenario from one preset without a confirmation', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const release = await openCard(user, 'release_gate')
    await user.click(within(release).getByRole('button', { name: 'Remove leak_check from release_gate' }))

    await waitFor(() => expect(mockUpdateSuite).toHaveBeenCalledTimes(1))
    expect(updatedSuite().entries.map((entry) => entry.preset_name)).toEqual(['weekly_smoke'])
    // The removed row took the focused button with it, so focus stays on the same card.
    await waitFor(() => expect(within(release).getByRole('button', { name: 'Add scenario to release_gate', hidden: true }))
      .toHaveFocus())
  })

  it('edits a scenario used only here directly', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const release = await openCard(user, 'release_gate')
    await user.click(within(release).getByRole('button', { name: 'Edit leak_check' }))

    expect(route()).toBe('/registry/scenario-presets/leak_check/edit')
  })

  it('warns before editing a scenario other presets share', async () => {
    const user = userEvent.setup()
    renderRegistry()

    const release = await openCard(user, 'release_gate')
    await user.click(within(release).getByRole('button', { name: 'Edit weekly_smoke' }))
    const warning = screen.getByRole('dialog', { name: 'airt.jailbreak is shared' })
    expect(warning).toHaveTextContent('This scenario is also used in quarterly_comparison.')
    expect(warning).toHaveTextContent('Your changes will apply to every preset that uses it.')
    await user.click(within(warning).getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(route()).toBe('/registry/scenario-presets')

    // The page leaves the accessibility tree hidden for a moment after a dialog closes.
    await user.click(await within(release).findByRole('button', { name: 'Edit weekly_smoke' }))
    await user.click(screen.getByRole('button', { name: 'Edit for all presets' }))
    expect(route()).toBe('/registry/scenario-presets/weekly_smoke/edit')
  })

  it('shows one New preset button in the empty state', async () => {
    const user = userEvent.setup()
    mockListSuites.mockResolvedValue({ source: 'suites', items: [] })
    renderRegistry()

    const empty = await screen.findByTestId('registry-presets-empty-state')
    expect(within(empty).getByRole('heading', { name: 'No presets yet' })).toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'New preset' })).toHaveLength(1)
    await user.click(within(empty).getByRole('button', { name: 'New preset' }))
    expect(screen.getByRole('dialog', { name: 'New preset' })).toBeInTheDocument()
  })

  it('reports a failed load with Retry', async () => {
    const user = userEvent.setup()
    mockListPresets.mockRejectedValueOnce(new Error('Preset storage is unavailable.'))
    renderRegistry()

    expect(await screen.findByText(/Couldn't load presets\. Preset storage is unavailable\./)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByTestId('registry-suite-release_gate')).toBeInTheDocument()
  })
})
