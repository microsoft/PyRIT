import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioSuitesApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { ScenarioSuiteResponse } from '@/types'

import ScenarioSuiteLibrary from './ScenarioSuiteLibrary'

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: {
    list: jest.fn(),
    remove: jest.fn(),
    launch: jest.fn(),
  },
}))

const mockList = scenarioSuitesApi.list as jest.Mock
const mockRemove = scenarioSuitesApi.remove as jest.Mock
const mockLaunch = scenarioSuitesApi.launch as jest.Mock
const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'
const OBJECTIVE_TARGET = makeTarget({ target_registry_name: 'objective-target' })

function makeSuite(): ScenarioSuiteResponse {
  return {
    suite: {
      name: 'nightly_suite',
      schema_version: 1,
      description: 'Nightly checks.',
      author: 'Ada Lovelace',
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'nightly_probe' },
      ],
    },
    origin: 'stored',
    version: 'suite-v1',
    entry_statuses: [
      {
        entry_id: FIRST_ENTRY_ID,
        preset_name: 'nightly_probe',
        origin: 'stored',
        preset_version: 'preset-v1',
        issues: [],
      },
      {
        entry_id: SECOND_ENTRY_ID,
        preset_name: 'nightly_probe',
        origin: 'stored',
        preset_version: 'preset-v1',
        issues: [],
      },
    ],
  }
}

function makeRegisteredSuite(): ScenarioSuiteResponse {
  return {
    ...makeSuite(),
    origin: 'registered',
    version: null,
  }
}

function renderLibrary({ defaultsReady = true }: { defaultsReady?: boolean } = {}) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        <ScenarioSuiteLibrary
          targets={[OBJECTIVE_TARGET]}
          defaultObjectiveTarget={OBJECTIVE_TARGET}
          defaultAdversarialTarget={null}
          labels={{}}
          operationId={null}
          defaultsReady={defaultsReady}
        />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScenarioSuiteLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockList.mockResolvedValue({ source: 'suites', items: [] })
    mockLaunch.mockResolvedValue({ suite_execution_id: 'execution-1' })
  })

  it('shows an empty state and configured storage source', async () => {
    renderLibrary()

    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
    expect(screen.getByText('Stored in suites')).toBeInTheDocument()
  })

  it('lists duplicate preset references, author, description, and launch controls', async () => {
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('2 entries')).toBeInTheDocument()
    expect(within(row).getByText('Nightly checks.')).toBeInTheDocument()
    expect(within(row).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Launch' })).toBeEnabled()
  })

  it('surfaces entry-scoped advisory issues', async () => {
    const suite = makeSuite()
    suite.entry_statuses[1] = {
      entry_id: SECOND_ENTRY_ID,
      preset_name: 'retired_probe',
      origin: null,
      preset_version: null,
      issues: [{ field: 'preset_name', message: "Preset 'retired_probe' is not available." }],
    }
    suite.suite.entries[1] = { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' }
    mockList.mockResolvedValue({ source: 'suites', items: [suite] })

    renderLibrary()

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText(
      "Entry 2 (retired_probe): Preset 'retired_probe' is not available.",
    )).toBeInTheDocument()
    expect(screen.getByTestId('launch-suite-nightly_suite')).toBeDisabled()
  })

  it('requires confirmation before deleting and refreshes afterwards', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValueOnce({ source: 'suites', items: [makeSuite()] })
    mockRemove.mockResolvedValue(undefined)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-suite-nightly_suite'))
    expect(mockRemove).not.toHaveBeenCalled()

    mockList.mockResolvedValueOnce({ source: 'suites', items: [] })
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith('nightly_suite'))
    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
  })

  it('identifies registered suites and exposes only a read-only view action', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'suites', items: [makeRegisteredSuite()] })

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('Defined in code')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'Launch' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: 'View' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeDisabled()

    await user.click(within(row).getByRole('button', { name: 'View' }))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/nightly_suite/edit')
    expect(mockRemove).not.toHaveBeenCalled()
  })

  it('opens the launch dialog for a runnable suite', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })

    renderLibrary()

    await user.click(await screen.findByTestId('launch-suite-nightly_suite'))

    expect(await screen.findByTestId('confirm-launch-suite')).toBeInTheDocument()
    expect(screen.getByTestId('launch-suite-entries')).toHaveTextContent(
      'nightly_probenightly_probe',
    )
  })

  it('refreshes the suite after a launch preflight conflict', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })
    mockLaunch.mockRejectedValue({
      isAxiosError: true,
      response: { status: 409, data: { detail: 'Suite changed.' } },
    })

    renderLibrary()

    await user.click(await screen.findByTestId('launch-suite-nightly_suite'))
    await user.click(screen.getByTestId('confirm-launch-suite'))

    expect(await screen.findByText(
      "Suite 'nightly_suite' or one of its presets changed while you were launching it. "
      + 'Review it and launch again.',
    )).toBeInTheDocument()
    await waitFor(() => expect(mockList).toHaveBeenCalledTimes(2))
  })

  it('blocks launch while the global launch context is unresolved', async () => {
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })

    renderLibrary({ defaultsReady: false })

    expect(await screen.findByTestId('launch-suite-nightly_suite')).toBeDisabled()
  })

  it('does not launch an empty suite', async () => {
    const suite = makeSuite()
    suite.suite.entries = []
    suite.entry_statuses = []
    mockList.mockResolvedValue({ source: 'suites', items: [suite] })

    renderLibrary()

    expect(await screen.findByText('Empty suite')).toBeInTheDocument()
    expect(screen.getByTestId('launch-suite-nightly_suite')).toBeDisabled()
  })

  it('reports load failures and retries', async () => {
    const user = userEvent.setup()
    mockList.mockRejectedValueOnce(new Error('suite storage unavailable'))

    renderLibrary()

    expect(await screen.findByText('suite storage unavailable')).toBeInTheDocument()
    mockList.mockResolvedValueOnce({ source: 'suites', items: [makeSuite()] })
    await user.click(screen.getByTestId('retry-suites-btn'))

    expect(await screen.findByTestId('suite-row-nightly_suite')).toBeInTheDocument()
  })

  it('navigates to create and edit routes', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })

    renderLibrary()

    await user.click(await screen.findByTestId('edit-suite-nightly_suite'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/nightly_suite/edit')

    await user.click(screen.getByTestId('new-suite-btn'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/new')
  })
})
