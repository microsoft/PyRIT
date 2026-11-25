import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makeRunSize } from '@/test-utils/scenarioSuiteFixtures'
import type { RegisteredScenario, ScenarioRunSizeEstimateResponse } from '@/types'

import ScenarioModuleCard from './ScenarioModuleCard'
import type { ScenarioModuleGroup } from './scenarioModules'

function garakScenario(name: string, overrides: Partial<RegisteredScenario> = {}): RegisteredScenario {
  return makeScenario({ scenario_name: `garak.${name}`, module: 'garak', ...overrides })
}

function garakGroup(scenarios: RegisteredScenario[]): ScenarioModuleGroup {
  return { key: 'garak', title: 'Garak', description: 'Ports of garak probes.', scenarios }
}

function ConfigurePage() {
  const location = useLocation()
  return <output aria-label="Configure page">{`${location.pathname} ${JSON.stringify(location.state)}`}</output>
}

interface RenderOptions {
  scenarios?: RegisteredScenario[]
  open?: boolean
  estimatesLoading?: boolean
  onOpenChange?: (open: boolean) => void
}

function renderCard(group: ScenarioModuleGroup, options: RenderOptions = {}) {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/scanner/launch?open=garak']}>
        <Routes>
          <Route
            path="/scanner/launch"
            element={(
              <ScenarioModuleCard
                group={group}
                scenarios={options.scenarios ?? group.scenarios}
                open={options.open ?? true}
                onOpenChange={options.onOpenChange ?? jest.fn()}
                estimatesLoading={options.estimatesLoading ?? false}
                linkState={{ launchSearch: '?open=garak' }}
              />
            )}
          />
          <Route path="/scanner/launch/:scenarioName" element={<ConfigurePage />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

function renderRow(scenario: RegisteredScenario, options: RenderOptions = {}): HTMLElement {
  renderCard(garakGroup([scenario]), options)
  return screen.getByTestId(`scenario-card-${scenario.scenario_name}`)
}

function withRunSize(runSize: Partial<ScenarioRunSizeEstimateResponse>): Partial<RegisteredScenario> {
  return { default_run_size: { ...makeRunSize(0), ...runSize } }
}

describe('ScenarioModuleCard', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('names the module, its scenario count, and its description while collapsed', async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    renderCard(garakGroup([garakScenario('encoding'), garakScenario('doctor')]), { open: false, onOpenChange })

    const toggle = screen.getByRole('button', { name: 'Garak 2 scenarios' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.getByRole('heading', { level: 3, name: 'Garak 2 scenarios' })).toBeInTheDocument()
    expect(screen.getByText('Ports of garak probes.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()

    await user.click(toggle)
    expect(onOpenChange).toHaveBeenCalledWith(true)
  })

  it('counts a single scenario and the matches out of the module total', () => {
    const encoding = garakScenario('encoding')
    renderCard(garakGroup([encoding, garakScenario('doctor'), garakScenario('divergence')]), { scenarios: [encoding] })

    expect(screen.getByRole('button', { name: 'Garak 1 of 3 scenarios' })).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Scenarios in Garak' })).getAllByRole('row')).toHaveLength(2)
  })

  it('renders the launch columns in order with the shared cell spacing', () => {
    const row = renderRow(garakScenario('encoding'))

    const headers = within(screen.getByRole('table', { name: 'Scenarios in Garak' })).getAllByRole('columnheader')
    expect(headers.map((header) => header.textContent)).toEqual([
      'Scenario / purpose',
      'Default datasets',
      'Default techniques',
      'Default run size',
    ])
    const cells = within(row).getAllByRole('cell')
    expect([...headers, ...cells].every((cell) => cell.classList.contains('scenario-catalog-cell-padding'))).toBe(true)
  })

  it('shows each scenario without its module prefix and the full id on hover', () => {
    const row = renderRow(garakScenario('encoding'))

    const link = within(row).getByRole('link', { name: 'encoding' })
    expect(link).toHaveAttribute('title', 'garak.encoding')
    expect(link).toHaveAttribute('href', '/scanner/launch/garak.encoding')
  })

  it('opens the encoded configure route carrying the Launch view', async () => {
    const user = userEvent.setup()
    renderCard({ key: 'other-scenarios', title: 'Other scenarios', description: '', scenarios: [
      makeScenario({ scenario_name: 'foundry/red_team_agent', module: null }),
    ] })

    await user.click(screen.getByRole('link', { name: 'foundry/red_team_agent' }))

    expect(screen.getByRole('status', { name: 'Configure page' })).toHaveTextContent(
      '/scanner/launch/foundry%2Fred_team_agent {"launchSearch":"?open=garak"}',
    )
  })

  it('renders Markdown descriptions and lets users expand long previews', async () => {
    const user = userEvent.setup()
    jest.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(60)
    jest.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(120)
    const row = renderRow(garakScenario('markdown', { description_markdown: 'Tests **formatted text** and `code`.' }))

    expect(within(row).getByText('formatted text').tagName).toBe('STRONG')
    const expand = within(row).getByRole('button', { name: 'Expand row details for markdown' })
    await user.click(expand)
    expect(within(row).getByRole('button', { name: 'Collapse row details for markdown' }))
      .toHaveAttribute('aria-expanded', 'true')
    jest.restoreAllMocks()
  })

  it('shows a spinner per row while default estimates are calculated', () => {
    const row = renderRow(garakScenario('pending'), { estimatesLoading: true })
    expect(within(row).getByText('Calculating...')).toBeInTheDocument()
  })

  it('shows configured limits and declared datasets rather than population counts', () => {
    const row = renderRow(garakScenario('compound', {
      default_datasets: ['population-a', 'population-b'],
      ...withRunSize({
        status: 'conditional',
        estimated_attack_count: null,
        components: [],
        dataset_size: { kind: 'bounded', value: 10 },
        datasets: [{
          name: 'population-a',
          kind: 'dataset',
          logical_seed_group_count: 100,
          selected_seed_group_count: 4,
          configured_caps: [],
          selection_note: null,
        }],
      }),
    }))

    expect(within(row).getByText('Up to 10 objectives')).toBeInTheDocument()
    expect(within(row).getByText('population-a · population-b')).toBeInTheDocument()
  })

  it('shows default techniques and an adaptive estimate as a plain attack range', () => {
    const row = renderRow(garakScenario('adaptive', {
      default_techniques: ['prompt_sending', 'jailbreak_system_prompt'],
      ...withRunSize({
        status: undefined,
        estimated_attack_count: null,
        minimum_attack_count: 21,
        maximum_attack_count: 42,
        components: [],
      }),
    }))

    expect(within(row).getByText('2 techniques')).toBeInTheDocument()
    expect(within(row).getByText('prompt_sending · jailbreak_system_prompt')).toBeInTheDocument()
    expect(within(row).getByText('21-42 attacks')).toBeInTheDocument()
  })

  it('says when a scenario has no default techniques or datasets', () => {
    const row = renderRow(garakScenario('bare', { default_techniques: [], default_datasets: [] }))

    expect(within(row).getByText('No default techniques')).toBeInTheDocument()
    expect(within(row).getByText('No default datasets')).toBeInTheDocument()
  })
})
