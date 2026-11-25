import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { makeScenario } from '@/test-utils/scenarioFixtures'
import { makePresetResponse, makeRunSize, makeSuite } from '@/test-utils/scenarioSuiteFixtures'
import type { ScenarioPresetResponse } from '@/types'

import SuiteCard from './SuiteCard'
import { suiteMembers } from './suiteMembers'

const SUITE = makeSuite('release_gate', ['weekly_smoke', 'copilot_gate'], {
  description: 'Everything we run before a model ships.',
})
const JAILBREAK = makeScenario({ scenario_name: 'airt.jailbreak', default_datasets: ['airt_harms'] })
const FOUNDRY = makeScenario({ scenario_name: 'foundry.red_team_agent', description: 'Foundry strategies.' })
const SCENARIOS = new Map([[JAILBREAK.scenario_name, JAILBREAK], [FOUNDRY.scenario_name, FOUNDRY]])

function presets(...items: ScenarioPresetResponse[]): Map<string, ScenarioPresetResponse> {
  return new Map(items.map((item) => [item.preset.name, item]))
}

const ESTIMATED = presets(
  makePresetResponse(
    { name: 'weekly_smoke', scenario_name: 'airt.jailbreak', description: 'Two templates, 30 objectives.' },
    { run_size: makeRunSize(90) },
  ),
  makePresetResponse({ name: 'copilot_gate', scenario_name: 'foundry.red_team_agent' }, { run_size: makeRunSize(400) }),
)

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>{children}</MemoryRouter></FluentProvider>
}

function renderCard(
  props: Partial<React.ComponentProps<typeof SuiteCard>> = {},
  presetMap: Map<string, ScenarioPresetResponse> = ESTIMATED,
) {
  const onLaunch = jest.fn()
  render(
    <TestWrapper>
      <SuiteCard
        item={SUITE}
        members={suiteMembers(SUITE, presetMap, SCENARIOS)}
        estimatesLoading={false}
        launchBlockedReason={null}
        onLaunch={onLaunch}
        {...props}
      />
    </TestWrapper>,
  )
  return { onLaunch }
}

describe('SuiteCard', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('heads the card with the preset, its scenario count, and its exact total', () => {
    renderCard()

    expect(screen.getByRole('heading', { level: 3, name: 'release_gate 2 scenarios · 490 attacks' })).toBeInTheDocument()
    expect(screen.getByText('Everything we run before a model ships.')).toBeInTheDocument()
  })

  it('omits the total while any scenario estimate is missing or not exact', () => {
    renderCard({}, presets(
      makePresetResponse({ name: 'weekly_smoke', scenario_name: 'airt.jailbreak' }, { run_size: makeRunSize(90) }),
      makePresetResponse(
        { name: 'copilot_gate', scenario_name: 'foundry.red_team_agent' },
        { run_size: makeRunSize(400, 'approximate') },
      ),
    ))

    expect(screen.getByRole('heading', { level: 3, name: 'release_gate 2 scenarios' })).toBeInTheDocument()
  })

  it('lists each scenario led by what it runs, with its stored name and own settings', async () => {
    const user = userEvent.setup()
    renderCard()

    await user.click(screen.getByRole('button', { name: /^release_gate/ }))

    const table = screen.getByRole('table', { name: 'Scenarios in release_gate' })
    expect(within(table).getAllByRole('columnheader').map((header) => header.textContent)).toEqual([
      'Scenario / purpose', 'Datasets', 'Techniques', 'Run size',
    ])
    const first = screen.getByTestId('suite-member-release_gate-entry-1')
    expect(within(first).getByText('airt.jailbreak')).toBeInTheDocument()
    expect(within(first).queryByRole('link')).not.toBeInTheDocument()
    expect(within(first).getByText('Saved as weekly_smoke')).toBeInTheDocument()
    expect(within(first).getByText('Two templates, 30 objectives.')).toBeInTheDocument()
    expect(within(first).getByText('airt_harms')).toBeInTheDocument()
    expect(within(first).getByText('90 attacks')).toBeInTheDocument()
    const second = screen.getByTestId('suite-member-release_gate-entry-2')
    expect(within(second).getByText('Foundry strategies.')).toBeInTheDocument()
  })

  it('launches the preset from its named button', async () => {
    const user = userEvent.setup()
    const { onLaunch } = renderCard()

    await user.click(screen.getByRole('button', { name: 'Launch release_gate' }))

    expect(onLaunch).toHaveBeenCalledWith(SUITE)
  })

  it('keeps a blocked Launch focusable and explains why', async () => {
    const user = userEvent.setup()
    const { onLaunch } = renderCard({ launchBlockedReason: 'Defaults are still loading.' })

    const launch = screen.getByRole('button', { name: 'Launch release_gate' })
    expect(launch).toHaveAttribute('aria-disabled', 'true')
    await user.tab()
    await user.tab()
    expect(launch).toHaveFocus()
    expect(launch).toHaveAccessibleDescription('Defaults are still loading.')
    await user.click(launch)
    expect(onLaunch).not.toHaveBeenCalled()
  })

  it('shows Calculating... per scenario while estimates load', async () => {
    const user = userEvent.setup()
    renderCard({ estimatesLoading: true })

    expect(screen.getByRole('heading', { level: 3, name: 'release_gate 2 scenarios' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /^release_gate/ }))
    expect(screen.getAllByText('Calculating...')).toHaveLength(2)
  })
})
