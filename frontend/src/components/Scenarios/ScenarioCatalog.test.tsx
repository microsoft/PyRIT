import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { scenariosApi } from '@/services/api'
import { installAutoRefreshTimers } from '@/test-utils/autoRefresh'
import { makeScenario } from '@/test-utils/scenarioFixtures'
import type { ListRegisteredScenariosResponse, RegisteredScenario, ScenarioModule } from '@/types'

import ScenarioCatalog from './ScenarioCatalog'

installAutoRefreshTimers()

jest.mock('@/services/api', () => ({
  scenariosApi: {
    listCatalog: jest.fn(),
    listModules: jest.fn(),
  },
}))

const mockListCatalog = scenariosApi.listCatalog as jest.Mock
const mockListModules = scenariosApi.listModules as jest.Mock

const MODULES: ScenarioModule[] = [
  { name: 'airt', display_name: 'AI Red Team', description: 'Harm-area scenarios from the AI Red Team.' },
  { name: 'garak', display_name: 'Garak', description: 'Ports of garak probes: prompt injection and more.' },
]

const SCENARIOS: RegisteredScenario[] = [
  makeScenario({ scenario_name: 'garak.encoding', module: 'garak', description: 'Hides objectives in encodings.' }),
  makeScenario({ scenario_name: 'garak.doctor', module: 'garak', description: 'Uses a fake doctor persona.' }),
  makeScenario({ scenario_name: 'airt.jailbreak', module: 'airt', description: 'Tries jailbreak templates.' }),
  makeScenario({ scenario_name: 'my_probe', module: null, description: 'A probe from an initializer.' }),
]

function catalogPage(items: RegisteredScenario[]): ListRegisteredScenariosResponse {
  return { items, pagination: { limit: 200, has_more: false } }
}

function LocationProbe() {
  const location = useLocation()
  return <output aria-label="Current route">{`${location.pathname}${location.search}`}</output>
}

function ConfigurePage() {
  const location = useLocation()
  return <output aria-label="Configure state">{JSON.stringify(location.state)}</output>
}

function renderCatalog(entry = '/scanner/launch') {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/scanner/launch" element={<ScenarioCatalog />} />
          <Route path="/scanner/launch/:scenarioName" element={<ConfigurePage />} />
        </Routes>
        <LocationProbe />
      </MemoryRouter>
    </FluentProvider>,
  )
}

function moduleToggle(name: RegExp): HTMLElement {
  return screen.getByRole('button', { name })
}

function currentRoute(): string | null {
  return screen.getByRole('status', { name: 'Current route' }).textContent
}

async function renderLoaded(entry?: string) {
  renderCatalog(entry)
  await screen.findByRole('heading', { level: 3, name: /^AI Red Team/ })
}

describe('ScenarioCatalog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockListCatalog.mockResolvedValue(catalogPage(SCENARIOS))
    mockListModules.mockResolvedValue({ items: MODULES })
  })

  it('shows a loading state while fetching', () => {
    mockListCatalog.mockReturnValue(new Promise(() => {}))
    renderCatalog()
    expect(screen.getByText('Loading scenarios...')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('titles the page in plain text with a link about scenarios', async () => {
    await renderLoaded()

    expect(screen.getByRole('heading', { level: 2, name: 'Launch a scan' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Launch a scan' })).not.toBeInTheDocument()
    expect(screen.getByText(/Choose a scenario to configure\./)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'About scenarios' }))
      .toHaveAttribute('href', 'https://microsoft.github.io/PyRIT/latest/scanner/scanner/')
  })

  it('lists collapsed module cards in the API order with other scenarios last', async () => {
    await renderLoaded()

    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent)).toEqual([
      'AI Red Team 1 scenario',
      'Garak 2 scenarios',
      'Other scenarios 1 scenario',
    ])
    for (const toggle of [/^AI Red Team/, /^Garak/, /^Other scenarios/]) {
      expect(moduleToggle(toggle)).toHaveAttribute('aria-expanded', 'false')
    }
    expect(screen.getByText('Ports of garak probes: prompt injection and more.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('keeps opened modules in the URL', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(moduleToggle(/^Garak/))
    await user.click(moduleToggle(/^AI Red Team/))

    expect(currentRoute()).toBe('/scanner/launch?open=garak&open=airt')
    expect(screen.getByRole('table', { name: 'Scenarios in Garak' })).toBeInTheDocument()
    await user.click(moduleToggle(/^Garak/))
    expect(currentRoute()).toBe('/scanner/launch?open=airt')
    expect(screen.queryByRole('table', { name: 'Scenarios in Garak' })).not.toBeInTheDocument()
  })

  it('restores the open modules from the URL', async () => {
    await renderLoaded('/scanner/launch?open=garak')

    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'true')
    expect(moduleToggle(/^AI Red Team/)).toHaveAttribute('aria-expanded', 'false')
    expect(within(screen.getByRole('table', { name: 'Scenarios in Garak' })).getByRole('link', { name: 'encoding' }))
      .toHaveAttribute('title', 'garak.encoding')
  })

  it('expands and collapses every module', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.click(screen.getByRole('button', { name: 'Expand all' }))
    expect(currentRoute()).toBe('/scanner/launch?open=airt&open=garak&open=other-scenarios')
    expect(screen.getAllByRole('table')).toHaveLength(3)

    await user.click(screen.getByRole('button', { name: 'Collapse all' }))
    expect(currentRoute()).toBe('/scanner/launch')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('carries the open modules to the configure page', async () => {
    const user = userEvent.setup()
    await renderLoaded('/scanner/launch?open=garak')

    await user.click(screen.getByRole('link', { name: 'doctor' }))

    expect(screen.getByRole('status', { name: 'Configure state' }))
      .toHaveTextContent('{"launchSearch":"?open=garak"}')
  })

  it('opens every module with a match while searching and counts the matches', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.type(screen.getByRole('textbox', { name: 'Search modules and scenarios' }), 'encodings')

    expect(screen.getAllByRole('heading', { level: 3 }).map((heading) => heading.textContent))
      .toEqual(['Garak 1 of 2 scenarios'])
    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('link', { name: 'encoding' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'doctor' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Expand all|Collapse all/ })).not.toBeInTheDocument()
    expect(screen.getByText('1 scenario matches "encodings"')).toBeInTheDocument()
    expect(currentRoute()).toBe('/scanner/launch')
  })

  it('lists a whole module when the search names it', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.type(screen.getByRole('textbox', { name: 'Search modules and scenarios' }), 'garak')

    expect(screen.getByRole('heading', { level: 3, name: 'Garak 2 scenarios' })).toBeInTheDocument()
    expect(screen.getAllByRole('link', { name: /^(encoding|doctor)$/ })).toHaveLength(2)
  })

  it('does not match module descriptions', async () => {
    const user = userEvent.setup()
    await renderLoaded()

    await user.type(screen.getByRole('textbox', { name: 'Search modules and scenarios' }), 'prompt injection')

    expect(screen.getByTestId('no-results-state')).toHaveTextContent('No scenarios match "prompt injection"')
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()
  })

  it('lets users close a matching module until the search changes', async () => {
    const user = userEvent.setup()
    await renderLoaded()
    const search = screen.getByRole('textbox', { name: 'Search modules and scenarios' })
    await user.type(search, 'garak')

    await user.click(moduleToggle(/^Garak/))
    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'false')
    expect(currentRoute()).toBe('/scanner/launch')

    await user.type(search, '.')
    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'true')
  })

  it('clears the search on Escape and restores the modules the user had open', async () => {
    const user = userEvent.setup()
    await renderLoaded('/scanner/launch?open=airt')
    const search = screen.getByRole('textbox', { name: 'Search modules and scenarios' })
    await user.type(search, 'encodings')
    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'true')

    await user.keyboard('{Escape}')

    expect(search).toHaveValue('')
    expect(moduleToggle(/^AI Red Team/)).toHaveAttribute('aria-expanded', 'true')
    expect(moduleToggle(/^Garak/)).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('button', { name: 'Expand all' })).toBeInTheDocument()
  })

  it('explains a load failure in plain words with the details and a retry', async () => {
    const user = userEvent.setup()
    mockListCatalog.mockRejectedValueOnce(new Error('initializer airt_scenarios timed out'))
    renderCatalog()

    expect(await screen.findByText("Couldn't load scenarios.")).toBeInTheDocument()
    expect(screen.getByText(/ask your PyRIT administrator to check the server/)).toBeInTheDocument()
    expect(screen.getByText('Details: initializer airt_scenarios timed out')).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('heading', { level: 3, name: /^Garak/ })).toBeInTheDocument()
    expect(screen.queryByText("Couldn't load scenarios.")).not.toBeInTheDocument()
  })

  it('warns when default estimates fail but keeps the modules', async () => {
    mockListCatalog
      .mockResolvedValueOnce(catalogPage(SCENARIOS))
      .mockRejectedValueOnce(new Error('estimate failed'))
    await renderLoaded()

    expect(await screen.findByText('estimate failed')).toBeInTheDocument()
    expect(moduleToggle(/^Garak/)).toBeInTheDocument()
  })

  it('shows an empty state without a search when no scenarios are registered', async () => {
    mockListCatalog.mockResolvedValue(catalogPage([]))
    mockListModules.mockResolvedValue({ items: [] })
    renderCatalog()

    expect(await screen.findByTestId('empty-state')).toHaveTextContent('No scenarios are registered')
    await waitFor(() => expect(mockListCatalog).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })
})
