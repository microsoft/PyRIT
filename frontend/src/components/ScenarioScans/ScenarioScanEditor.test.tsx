import type { ReactElement } from 'react'

import { FluentProvider, useFocusFinders, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  createMemoryRouter,
  RouterProvider,
  useLocation,
  useNavigate,
} from 'react-router'

import { scenarioPresetsApi, scenarioScansApi } from '@/services/api'
import type { ScenarioPresetResponse, ScenarioScanResponse } from '@/types'
import { generateClientId } from '@/utils/clientId'

import ScenarioScanEditor from './ScenarioScanEditor'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: {
    list: jest.fn(),
  },
  scenarioScansApi: {
    get: jest.fn(),
    create: jest.fn(),
    update: jest.fn(),
  },
}))

jest.mock('@/utils/clientId', () => ({
  generateClientId: jest.fn(),
}))

const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockGetScan = scenarioScansApi.get as jest.Mock
const mockCreateScan = scenarioScansApi.create as jest.Mock
const mockUpdateScan = scenarioScansApi.update as jest.Mock
const mockGenerateClientId = generateClientId as jest.Mock

const FIRST_ENTRY_ID = '11111111-1111-4111-8111-111111111111'
const SECOND_ENTRY_ID = '22222222-2222-4222-8222-222222222222'
const THIRD_ENTRY_ID = '33333333-3333-4333-8333-333333333333'

const AVAILABLE_PRESETS: ScenarioPresetResponse[] = [
  {
    preset: { name: 'nightly_probe', scenario_name: 'foundry.red_team_agent' },
    origin: 'stored',
    version: 'preset-v1',
    issues: [],
  },
  {
    preset: { name: 'weekly_probe', scenario_name: 'foundry.encoding' },
    origin: 'registered',
    version: null,
    issues: [],
  },
]

function makeScan(): ScenarioScanResponse {
  return {
    scan: {
      name: 'nightly_scan',
      schema_version: 1,
      description: 'Nightly checks.',
      author: 'Ada Lovelace',
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' },
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
        preset_name: 'retired_probe',
        origin: null,
        preset_version: null,
        issues: [{ field: 'preset_name', message: "Preset 'retired_probe' is not available." }],
      },
    ],
  }
}

function apiError(status: number, detail: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: { detail } },
  }
}

const originalRequest = globalThis.Request

class RouterTestRequest {
  readonly url: string
  readonly method: string
  readonly signal: AbortSignal

  constructor(input: string | URL, init?: { method?: string, signal?: AbortSignal | null }) {
    this.url = String(input)
    this.method = init?.method ?? 'GET'
    this.signal = init?.signal ?? new AbortController().signal
  }
}

function RouterProbe(): ReactElement {
  useFocusFinders()
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <output aria-label="Current URL">{location.pathname}</output>
      <button type="button" onClick={() => void navigate('/other')}>Leave editor</button>
    </>
  )
}

function editorElement(mode: 'create' | 'edit'): ReactElement {
  return (
    <>
      <ScenarioScanEditor mode={mode} />
      <RouterProbe />
    </>
  )
}

function renderEditor(mode: 'create' | 'edit', name = 'nightly_scan') {
  const initialPath = mode === 'create'
    ? '/registry/scenario-scans/new'
    : `/registry/scenario-scans/${name}/edit`
  const router = createMemoryRouter([
    {
      path: '/registry/scenario-scans/new',
      element: editorElement('create'),
    },
    {
      path: '/registry/scenario-scans/:scanName/edit',
      element: editorElement('edit'),
    },
    {
      path: '/registry/scenario-scans',
      element: <><h1>Scan library</h1><RouterProbe /></>,
    },
    {
      path: '/other',
      element: <><h1>Other page</h1><RouterProbe /></>,
    },
  ], { initialEntries: [initialPath] })

  render(
    <FluentProvider theme={webLightTheme}>
      <RouterProvider router={router} />
    </FluentProvider>,
  )
  return router
}

async function addPreset(
  user: ReturnType<typeof userEvent.setup>,
  presetName: string,
): Promise<void> {
  await user.click(screen.getByTestId('scan-preset-select'))
  await user.click(await screen.findByRole('option', { name: presetName }))
  await user.click(screen.getByTestId('add-scan-entry'))
}

describe('ScenarioScanEditor', () => {
  beforeAll(() => {
    Object.defineProperty(globalThis, 'Request', {
      configurable: true,
      writable: true,
      value: RouterTestRequest,
    })
  })

  afterAll(() => {
    Object.defineProperty(globalThis, 'Request', {
      configurable: true,
      writable: true,
      value: originalRequest,
    })
  })

  beforeEach(() => {
    jest.clearAllMocks()
    mockListPresets.mockResolvedValue({ source: 'presets', items: AVAILABLE_PRESETS })
    mockGetScan.mockResolvedValue(makeScan())
    mockCreateScan.mockResolvedValue(makeScan())
    mockUpdateScan.mockResolvedValue({ ...makeScan(), version: 'scan-v2' })
    mockGenerateClientId.mockReturnValue(THIRD_ENTRY_ID)
  })

  it('creates duplicate preset references with distinct stable entry IDs', async () => {
    const user = userEvent.setup()
    mockGenerateClientId
      .mockReturnValueOnce(FIRST_ENTRY_ID)
      .mockReturnValueOnce(FIRST_ENTRY_ID)
      .mockReturnValueOnce(SECOND_ENTRY_ID)
    renderEditor('create')

    await user.type(await screen.findByTestId('scan-name-input'), 'nightly_scan')
    await addPreset(user, 'nightly_probe')
    await addPreset(user, 'nightly_probe')
    await user.click(screen.getByTestId('save-scan-btn'))

    await waitFor(() => expect(mockCreateScan).toHaveBeenCalledWith({
      name: 'nightly_scan',
      schema_version: 1,
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'nightly_probe' },
      ],
    }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent('/registry/scenario-scans')
  })

  it('shows projected status and saves dangling references in reordered entry order', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    expect(await screen.findByTestId('scenario-scan-editor')).toBeInTheDocument()
    expect(screen.getByText('Schema version 1 · Author: Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText("Preset 'retired_probe' is not available.")).toBeInTheDocument()
    expect(screen.getByText('Current preset version: preset-v1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Move entry 2 up' }))
    await user.click(screen.getByTestId('save-scan-btn'))

    await waitFor(() => expect(mockUpdateScan).toHaveBeenCalledWith(
      'nightly_scan',
      {
        name: 'nightly_scan',
        schema_version: 1,
        description: 'Nightly checks.',
        author: 'Ada Lovelace',
        entries: [
          { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' },
          { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        ],
      },
      'scan-v1',
    ))
  })

  it('removes entries and adds a registered preset without changing existing IDs', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await screen.findByTestId('scenario-scan-editor')
    await user.click(screen.getByRole('button', { name: 'Remove entry 2' }))
    await addPreset(user, 'weekly_probe')
    await user.click(screen.getByTestId('save-scan-btn'))

    await waitFor(() => expect(mockUpdateScan).toHaveBeenCalledWith(
      'nightly_scan',
      expect.objectContaining({
        entries: [
          { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
          { entry_id: THIRD_ENTRY_ID, preset_name: 'weekly_probe' },
        ],
      }),
      'scan-v1',
    ))
  })

  it('reports a version conflict without navigating away', async () => {
    const user = userEvent.setup()
    mockUpdateScan.mockRejectedValue(
      apiError(409, "Scenario scan 'nightly_scan' changed since it was read; re-read it and retry"),
    )
    renderEditor('edit')

    const description = await screen.findByTestId('scan-description-input')
    await user.type(description, ' Updated.')
    await user.click(screen.getByTestId('save-scan-btn'))

    expect(await screen.findByText(
      "Scenario scan 'nightly_scan' changed since it was read; re-read it and retry",
    )).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-scans/nightly_scan/edit',
    )
  })

  it('blocks in-app navigation until unsaved edits are discarded', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await user.type(await screen.findByTestId('scan-description-input'), ' Updated.')
    await user.click(await screen.findByRole('button', { name: 'Leave editor' }))

    expect(await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-scans/nightly_scan/edit',
    )

    await user.click(await screen.findByRole('button', { name: 'Leave editor' }))
    await user.click(await screen.findByRole('button', { name: 'Discard changes' }))
    expect(await screen.findByRole('heading', { name: 'Other page' })).toBeInTheDocument()
  })

  it('requests browser confirmation before unloading unsaved edits', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await user.type(await screen.findByTestId('scan-description-input'), ' Updated.')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
  })

  it('leaves a clean editor without a discard prompt', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await screen.findByTestId('scenario-scan-editor')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('heading', { name: 'Scan library' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
  })

  it('rejects an invalid name without calling the API', async () => {
    const user = userEvent.setup()
    renderEditor('create')

    await user.type(await screen.findByTestId('scan-name-input'), 'Nightly-Scan')
    await user.click(screen.getByTestId('save-scan-btn'))

    expect(await screen.findByText(/Use lowercase letters/)).toBeInTheDocument()
    expect(mockCreateScan).not.toHaveBeenCalled()
  })

  it('reports a missing scan and returns to the library', async () => {
    const user = userEvent.setup()
    mockGetScan.mockRejectedValue(apiError(404, 'No such scan.'))
    renderEditor('edit')

    expect(await screen.findByTestId('scan-editor-error-state')).toHaveTextContent(
      'No scan named "nightly_scan".',
    )
    await user.click(screen.getByRole('button', { name: 'Back to scans' }))
    expect(await screen.findByRole('heading', { name: 'Scan library' })).toBeInTheDocument()
  })
})
