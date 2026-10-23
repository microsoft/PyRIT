import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioSuitesApi } from '@/services/api'
import type { ScenarioSuiteResponse } from '@/types'

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

function renderLibrary() {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter>
        <ScenarioSuiteLibrary />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScenarioSuiteLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockList.mockResolvedValue({ source: 'suites', items: [] })
  })

  it('shows an empty state and configured storage source', async () => {
    renderLibrary()

    expect(await screen.findByTestId('suite-empty-state')).toBeInTheDocument()
    expect(screen.getByText('Stored in suites')).toBeInTheDocument()
  })

  it('lists duplicate preset references, author, and description without launch controls', async () => {
    mockList.mockResolvedValue({ source: 'suites', items: [makeSuite()] })

    renderLibrary()

    const row = await screen.findByTestId('suite-row-nightly_suite')
    expect(within(row).getByText('2 entries')).toBeInTheDocument()
    expect(within(row).getByText('Nightly checks.')).toBeInTheDocument()
    expect(within(row).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /launch/i })).not.toBeInTheDocument()
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
