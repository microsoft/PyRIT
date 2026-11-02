import type { ReactNode } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioPresetsApi, scenarioSuitesApi, scenariosApi } from '@/services/api'
import type {
  ScenarioPresetResponse,
  ScenarioRunSizeEstimateResponse,
  ScenarioSuiteResponse,
} from '@/types'

import ScenarioPresetRegistry from './ScenarioPresetRegistry'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: {
    list: jest.fn(),
    remove: jest.fn(),
    update: jest.fn(),
  },
  scenarioSuitesApi: {
    list: jest.fn(),
    remove: jest.fn(),
  },
  scenariosApi: {
    estimateRun: jest.fn(),
  },
}))

const mockPresetList = scenarioPresetsApi.list as jest.Mock
const mockPresetRemove = scenarioPresetsApi.remove as jest.Mock
const mockSuiteList = scenarioSuitesApi.list as jest.Mock
const mockEstimateRun = scenariosApi.estimateRun as jest.Mock

const ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const MISSING_PRESET_ISSUE = "Entry 1 (nightly_probe): Preset 'nightly_probe' is not available."

const PRESET: ScenarioPresetResponse = {
  preset: { name: 'nightly_probe', scenario_name: 'foundry.red_team_agent', techniques: ['crescendo'] },
  origin: 'stored',
  version: 'v1',
  issues: [],
  run_size: null,
}

const RUN_SIZE: ScenarioRunSizeEstimateResponse = {
  status: 'exact',
  dataset_size: { kind: 'bounded', value: 12 },
  dataset_limit: { state: 'value', value: 12 },
  estimated_attack_count: 12,
  components: [{ label: 'Crescendo', count: 12, is_baseline: false, note: null }],
  datasets: [],
  note: null,
}

function makeSuite(presetAvailable: boolean): ScenarioSuiteResponse {
  return {
    suite: {
      name: 'nightly_suite',
      schema_version: 1,
      entries: [{ entry_id: ENTRY_ID, preset_name: 'nightly_probe' }],
    },
    origin: 'stored',
    version: 'suite-v1',
    entry_statuses: [presetAvailable
      ? {
        entry_id: ENTRY_ID,
        preset_name: 'nightly_probe',
        origin: 'stored',
        preset_version: 'v1',
        issues: [],
      }
      : {
        entry_id: ENTRY_ID,
        preset_name: 'nightly_probe',
        origin: null,
        preset_version: null,
        issues: [{ field: 'preset_name', message: "Preset 'nightly_probe' is not available." }],
      }],
  }
}

function TestWrapper({ children }: { children: ReactNode }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>{children}</MemoryRouter>
    </FluentProvider>
  )
}

function renderRegistry() {
  return render(<ScenarioPresetRegistry />, { wrapper: TestWrapper })
}

async function deletePreset(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(await screen.findByTestId('delete-preset-nightly_probe'))
  const dialog = await screen.findByRole('dialog')
  await user.click(within(dialog).getByRole('button', { name: 'Delete' }))
}

describe('ScenarioPresetRegistry', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    for (const mock of [mockPresetList, mockPresetRemove, mockSuiteList, mockEstimateRun]) {
      mock.mockReset()
    }
    mockPresetList.mockResolvedValue({ source: 'presets.yaml', items: [] })
    mockSuiteList.mockResolvedValue({ source: 'suites', items: [] })
    mockEstimateRun.mockResolvedValue(RUN_SIZE)
  })

  it('titles the page and points launches to the Scanner', async () => {
    renderRegistry()

    expect(screen.getByRole('heading', { level: 1, name: 'Scenario presets' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Scanner' })).toHaveAttribute('href', '/scanner')
    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
  })

  it('shows the suites before the individual presets they are built from', async () => {
    renderRegistry()

    await screen.findByTestId('suite-empty-state')
    await screen.findByTestId('empty-state')
    const suites = screen.getByRole('region', { name: 'Scenario suites' })
    const presets = screen.getByRole('region', { name: 'Individual presets' })
    expect(suites.compareDocumentPosition(presets)).toBe(Node.DOCUMENT_POSITION_FOLLOWING)
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading: HTMLElement) => heading.textContent)).toEqual([
      'Scenario suites',
      'Individual presets',
    ])
  })

  it('reloads suite entry statuses after a preset is deleted', async () => {
    const user = userEvent.setup()
    mockPresetList
      .mockResolvedValueOnce({ source: 'presets.yaml', items: [PRESET] })
      .mockResolvedValueOnce({ source: 'presets.yaml', items: [] })
    mockSuiteList
      .mockResolvedValueOnce({ source: 'suites', items: [makeSuite(true)] })
      .mockResolvedValueOnce({ source: 'suites', items: [makeSuite(false)] })
    mockPresetRemove.mockResolvedValueOnce(undefined)

    renderRegistry()

    expect(await screen.findByText('No advisory issues')).toBeInTheDocument()
    expect(mockSuiteList).toHaveBeenCalledTimes(1)

    await deletePreset(user)

    expect(await screen.findByText(MISSING_PRESET_ISSUE)).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('1 advisory issue')).toBeInTheDocument()
    expect(await screen.findByTestId('empty-state')).toBeInTheDocument()
    expect(mockPresetRemove).toHaveBeenCalledWith('nightly_probe')
    expect(mockSuiteList).toHaveBeenCalledTimes(2)
  })

  it('leaves suite entry statuses alone when a preset delete fails', async () => {
    const user = userEvent.setup()
    mockPresetList.mockResolvedValue({ source: 'presets.yaml', items: [PRESET] })
    mockSuiteList.mockResolvedValue({ source: 'suites', items: [makeSuite(true)] })
    mockPresetRemove.mockRejectedValueOnce(new Error('Preset storage is read-only.'))

    renderRegistry()

    expect(await screen.findByText('No advisory issues')).toBeInTheDocument()

    await deletePreset(user)

    expect(await screen.findByText('Preset storage is read-only.')).toBeInTheDocument()
    expect(screen.getByText('No advisory issues')).toBeInTheDocument()
    expect(mockSuiteList).toHaveBeenCalledTimes(1)
  })
})
