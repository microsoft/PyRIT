import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { scenarioScansApi } from '@/services/api'
import type { ScenarioScanResponse } from '@/types'

import ScenarioScanLibrary from './ScenarioScanLibrary'

jest.mock('@/services/api', () => ({
  scenarioScansApi: {
    list: jest.fn(),
    remove: jest.fn(),
  },
}))

const mockList = scenarioScansApi.list as jest.Mock
const mockRemove = scenarioScansApi.remove as jest.Mock
const mockNavigate = jest.fn()

jest.mock('react-router', () => ({
  ...jest.requireActual('react-router'),
  useNavigate: () => mockNavigate,
}))

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'

function makeScan(): ScenarioScanResponse {
  return {
    scan: {
      name: 'nightly_scan',
      schema_version: 1,
      description: 'Nightly checks.',
      author: 'Ada Lovelace',
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'nightly_probe' },
      ],
    },
    version: 'scan-v1',
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
        <ScenarioScanLibrary />
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('ScenarioScanLibrary', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockList.mockResolvedValue({ source: 'scans', items: [] })
  })

  it('shows an empty state and configured storage source', async () => {
    renderLibrary()

    expect(await screen.findByTestId('scan-empty-state')).toBeInTheDocument()
    expect(screen.getByText('Stored in scans')).toBeInTheDocument()
  })

  it('lists duplicate preset references, author, and description without launch controls', async () => {
    mockList.mockResolvedValue({ source: 'scans', items: [makeScan()] })

    renderLibrary()

    const row = await screen.findByTestId('scan-row-nightly_scan')
    expect(within(row).getByText('2 entries')).toBeInTheDocument()
    expect(within(row).getByText('Nightly checks.')).toBeInTheDocument()
    expect(within(row).getByText('Ada Lovelace')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /launch/i })).not.toBeInTheDocument()
  })

  it('surfaces entry-scoped advisory issues', async () => {
    const scan = makeScan()
    scan.entry_statuses[1] = {
      entry_id: SECOND_ENTRY_ID,
      preset_name: 'retired_probe',
      origin: null,
      preset_version: null,
      issues: [{ field: 'preset_name', message: "Preset 'retired_probe' is not available." }],
    }
    scan.scan.entries[1] = { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' }
    mockList.mockResolvedValue({ source: 'scans', items: [scan] })

    renderLibrary()

    expect(await screen.findByText('Needs attention')).toBeInTheDocument()
    expect(screen.getByText(
      "Entry 2 (retired_probe): Preset 'retired_probe' is not available.",
    )).toBeInTheDocument()
  })

  it('requires confirmation before deleting and refreshes afterwards', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValueOnce({ source: 'scans', items: [makeScan()] })
    mockRemove.mockResolvedValue(undefined)

    renderLibrary()

    await user.click(await screen.findByTestId('delete-scan-nightly_scan'))
    expect(mockRemove).not.toHaveBeenCalled()

    mockList.mockResolvedValueOnce({ source: 'scans', items: [] })
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(mockRemove).toHaveBeenCalledWith('nightly_scan'))
    expect(await screen.findByTestId('scan-empty-state')).toBeInTheDocument()
  })

  it('reports load failures and retries', async () => {
    const user = userEvent.setup()
    mockList.mockRejectedValueOnce(new Error('scan storage unavailable'))

    renderLibrary()

    expect(await screen.findByText('scan storage unavailable')).toBeInTheDocument()
    mockList.mockResolvedValueOnce({ source: 'scans', items: [makeScan()] })
    await user.click(screen.getByTestId('retry-scans-btn'))

    expect(await screen.findByTestId('scan-row-nightly_scan')).toBeInTheDocument()
  })

  it('navigates to create and edit routes', async () => {
    const user = userEvent.setup()
    mockList.mockResolvedValue({ source: 'scans', items: [makeScan()] })

    renderLibrary()

    await user.click(await screen.findByTestId('edit-scan-nightly_scan'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-scans/nightly_scan/edit')

    await user.click(screen.getByTestId('new-scan-btn'))
    expect(mockNavigate).toHaveBeenCalledWith('/registry/scenario-scans/new')
  })
})
