import type { ReactNode } from 'react'

import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioSuitesApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { ScenarioSuiteListResponse, ScenarioSuiteResponse } from '@/types'

import ScenarioSuiteLibrary from './ScenarioSuiteLibrary'

jest.mock('@/services/api', () => ({
  scenarioSuitesApi: {
    list: jest.fn(),
    remove: jest.fn(),
  },
}))

const mockList = scenarioSuitesApi.list as jest.Mock
const mockRemove = scenarioSuitesApi.remove as jest.Mock
const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'
const DEFINED_IN_CODE_HINT = 'Defined in code, so it cannot be edited or deleted here.'
const MISSING_PRESET_ISSUE = "Entry 1 (nightly_probe): Preset 'nightly_probe' is not available."

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

function makeSuiteWithMissingPreset(): ScenarioSuiteResponse {
  const suite = makeSuite()
  suite.entry_statuses[0] = {
    entry_id: FIRST_ENTRY_ID,
    preset_name: 'nightly_probe',
    origin: null,
    preset_version: null,
    issues: [{ field: 'preset_name', message: "Preset 'nightly_probe' is not available." }],
  }
  return suite
}

function listOf(...items: ScenarioSuiteResponse[]): ScenarioSuiteListResponse {
  return { source: 'suites', items }
}

function TestWrapper({ children }: { children: ReactNode }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>{children}</MemoryRouter>
    </FluentProvider>
  )
}

function renderLibrary(presetsRevision = 0) {
  return render(<ScenarioSuiteLibrary presetsRevision={presetsRevision} />, { wrapper: TestWrapper })
}

describe('ScenarioSuiteLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockList.mockReset()
    mockRemove.mockReset()
    mockList.mockResolvedValue(listOf())
  })

  it('shows an empty state and configured storage source', async () => {
    renderLibrary()

    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
    expect(screen.getByText('Stored in suites')).toBeInTheDocument()
  })

  it('heads its section "Scenario suites" and offers no launch action', async () => {
    mockList.mockResolvedValue(listOf(makeSuite()))

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(screen.getByRole('heading', { level: 2, name: 'Scenario suites' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Scenario suites' })).toContainElement(row)
    expect(screen.queryByRole('button', { name: /launch/i })).not.toBeInTheDocument()
    expect(screen.queryByTestId('launch-suite-nightly_suite')).not.toBeInTheDocument()
  })

  it('lists duplicate preset references, author, and description', async () => {
    mockList.mockResolvedValue(listOf(makeSuite()))

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('2 entries')).toBeInTheDocument()
    expect(within(row).getByText('Nightly checks.')).toBeInTheDocument()
    expect(within(row).getByRole('link', { name: 'nightly_suite' })).toHaveAttribute(
      'href',
      '/registry/scenario-suites/nightly_suite/edit',
    )
    expect(within(screen.getByTestId('suite-author-nightly_suite')).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('No advisory issues')).toBeInTheDocument()
    expect(within(row).queryByText('Needs attention')).not.toBeInTheDocument()
    expect(within(row).queryByText('Defined in code')).not.toBeInTheDocument()
    expect(screen.queryByTestId('suite-issues-nightly_suite')).not.toBeInTheDocument()
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
    mockList.mockResolvedValue(listOf(suite))

    renderLibrary()

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText(
      "Entry 2 (retired_probe): Preset 'retired_probe' is not available.",
    )).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('1 advisory issue')).toBeInTheDocument()
  })

  it('counts advisory issues across every entry', async () => {
    const suite = makeSuiteWithMissingPreset()
    suite.entry_statuses[1] = {
      ...suite.entry_statuses[1],
      issues: [{ field: 'scenario_name', message: 'Unknown scenario "foundry.retired".' }],
    }
    mockList.mockResolvedValue(listOf(suite))

    renderLibrary()

    const issues = await screen.findByTestId('suite-issues-nightly_suite')
    expect(within(issues).getAllByRole('listitem').map((item: HTMLElement) => item.textContent)).toEqual([
      MISSING_PRESET_ISSUE,
      'Entry 2 (nightly_probe): Unknown scenario "foundry.retired".',
    ])
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('2 advisory issues')).toBeInTheDocument()
  })

  it('describes an empty suite', async () => {
    const suite = makeSuite()
    suite.suite.entries = []
    suite.entry_statuses = []
    mockList.mockResolvedValue(listOf(suite))

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('0 entries')).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('Empty suite')).toBeInTheDocument()
  })

  it('names a single entry and leaves the author blank without one', async () => {
    const suite = makeSuite()
    suite.suite = {
      name: 'nightly_suite',
      schema_version: 1,
      entries: [suite.suite.entries[0]],
    }
    suite.entry_statuses = [suite.entry_statuses[0]]
    mockList.mockResolvedValue(listOf(suite))

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('1 entry')).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-author-nightly_suite')).getByText('—')).toBeInTheDocument()
    expect(within(row).queryByText('Nightly checks.')).not.toBeInTheDocument()
  })

  it('requires confirmation before deleting and refreshes afterwards', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValueOnce(listOf(makeSuite()))
    mockRemove.mockResolvedValue(undefined)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-suite-nightly_suite'))
    expect(mockRemove).not.toHaveBeenCalled()

    mockList.mockResolvedValueOnce(listOf())
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('Delete nightly_suite?')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
    expect(mockRemove).toHaveBeenCalledWith('nightly_suite')
    expect(mockList).toHaveBeenCalledTimes(2)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('holds the confirmation until the server finishes deleting', async () => {
    const user = userEvent.setup()
    const removal = deferred<void>()
    mockList.mockResolvedValueOnce(listOf(makeSuite()))
    mockRemove.mockReturnValueOnce(removal.promise)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-suite-nightly_suite'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(screen.getByTestId('confirm-delete-suite')).toHaveTextContent('Deleting...')
    expect(screen.getByTestId('confirm-delete-suite')).toBeDisabled()
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toBeDisabled()

    mockList.mockResolvedValueOnce(listOf())
    await act(async () => { removal.resolve(undefined) })

    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
  })

  it('reports a delete failure without removing the suite', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue(listOf(makeSuite()))
    mockRemove.mockRejectedValueOnce(new Error('Suite storage is read-only.'))

    renderLibrary()

    await user.click(await screen.findByTestId('delete-suite-nightly_suite'))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('Suite storage is read-only.')).toBeInTheDocument()
    expect(screen.getByTestId('suite-row-nightly_suite')).toBeInTheDocument()
    expect(screen.getByTestId('confirm-delete-suite')).toBeEnabled()
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it.each(['Cancel', 'Escape'])('closes the delete confirmation with %s without deleting', async (dismiss: string) => {
    const user = userEvent.setup()
    mockList.mockResolvedValue(listOf(makeSuite()))

    renderLibrary()

    await user.click(await screen.findByTestId('delete-suite-nightly_suite'))
    const dialog = await screen.findByRole('dialog')
    if (dismiss === 'Escape') await user.keyboard('{Escape}')
    else await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(mockRemove).not.toHaveBeenCalled()
    expect(screen.getByTestId('suite-row-nightly_suite')).toBeInTheDocument()
  })

  it('identifies registered suites and exposes only a read-only view action', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue(listOf(makeRegisteredSuite()))

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('Defined in code')).toBeInTheDocument()
    expect(within(row).getByRole('button', { name: 'View' })).toBeEnabled()
    expect(within(row).getByRole('button', { name: 'Delete' })).toBeDisabled()
    expect(within(row).getByRole('button', { name: 'Delete' })).toHaveAttribute('title', DEFINED_IN_CODE_HINT)
    expect(within(row).queryByRole('button', { name: 'Edit' })).not.toBeInTheDocument()

    await user.click(within(row).getByRole('button', { name: 'View' }))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/nightly_suite/edit')
    expect(mockRemove).not.toHaveBeenCalled()
  })

  it('reports load failures and retries', async () => {
    const user = userEvent.setup()
    mockList.mockRejectedValueOnce(new Error('suite storage unavailable'))

    renderLibrary()

    const failure = await screen.findByTestId('suite-error-state')
    expect(within(failure).getByText('suite storage unavailable')).toBeInTheDocument()
    mockList.mockResolvedValueOnce(listOf(makeSuite()))
    await user.click(screen.getByTestId('retry-suites-btn'))

    expect(await screen.findByTestId('suite-row-nightly_suite')).toBeInTheDocument()
    expect(screen.queryByTestId('suite-error-state')).not.toBeInTheDocument()
  })

  it('navigates to create and edit routes', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue(listOf(makeSuite()))

    renderLibrary()

    await user.click(await screen.findByTestId('edit-suite-nightly_suite'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/nightly_suite/edit')

    await user.click(screen.getByTestId('new-suite-btn'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-suites/new')
  })

  it('reloads the suites only when the presets revision changes', async () => {
    mockList
      .mockResolvedValueOnce(listOf(makeSuite()))
      .mockResolvedValueOnce(listOf(makeSuiteWithMissingPreset()))

    const { rerender } = renderLibrary()

    expect(await screen.findByText('No advisory issues')).toBeInTheDocument()

    rerender(<ScenarioSuiteLibrary presetsRevision={0} />)
    expect(mockList).toHaveBeenCalledTimes(1)

    rerender(<ScenarioSuiteLibrary presetsRevision={1} />)

    expect(await screen.findByText(MISSING_PRESET_ISSUE)).toBeInTheDocument()
    expect(within(screen.getByTestId('suite-status-nightly_suite')).getByText('1 advisory issue')).toBeInTheDocument()
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it.each(['success', 'failure'])(
    'ignores an older load that finishes with %s after the presets revision changes',
    async (outcome: string) => {
      const stale = deferred<ScenarioSuiteListResponse>()
      mockList
        .mockReturnValueOnce(stale.promise)
        .mockResolvedValueOnce(listOf(makeSuiteWithMissingPreset()))

      const { rerender } = renderLibrary()
      const signal: AbortSignal = mockList.mock.calls[0][0]

      rerender(<ScenarioSuiteLibrary presetsRevision={1} />)

      expect(await screen.findByText(MISSING_PRESET_ISSUE)).toBeInTheDocument()
      expect(signal.aborted).toBe(true)

      await act(async () => {
        if (outcome === 'success') stale.resolve(listOf(makeSuite()))
        else stale.reject(new Error('suite storage unavailable'))
      })

      expect(screen.getByText(MISSING_PRESET_ISSUE)).toBeInTheDocument()
      expect(screen.queryByText('No advisory issues')).not.toBeInTheDocument()
      expect(screen.queryByTestId('suite-error-state')).not.toBeInTheDocument()
      expect(mockList).toHaveBeenCalledTimes(2)
    },
  )
})
