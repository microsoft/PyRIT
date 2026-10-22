import type { ReactElement } from 'react'

import { FluentProvider, useFocusFinders, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import {
  createMemoryRouter,
  RouterProvider,
  useLocation,
  useNavigate,
} from 'react-router'

import { scenarioPresetsApi, scenarioSuitesApi } from '@/services/api'
import type { ScenarioPresetResponse, ScenarioSuiteResponse } from '@/types'
import { generateClientId } from '@/utils/clientId'

import ScenarioSuiteEditor from './ScenarioSuiteEditor'

jest.mock('@/services/api', () => ({
  scenarioPresetsApi: {
    list: jest.fn(),
  },
  scenarioSuitesApi: {
    get: jest.fn(),
    create: jest.fn(),
    update: jest.fn(),
  },
}))

jest.mock('@/utils/clientId', () => ({
  generateClientId: jest.fn(),
}))

const mockListPresets = scenarioPresetsApi.list as jest.Mock
const mockGetSuite = scenarioSuitesApi.get as jest.Mock
const mockCreateSuite = scenarioSuitesApi.create as jest.Mock
const mockUpdateSuite = scenarioSuitesApi.update as jest.Mock
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

function makeSuite(): ScenarioSuiteResponse {
  return {
    suite: {
      name: 'nightly_suite',
      schema_version: 1,
      description: 'Nightly checks.',
      author: 'Ada Lovelace',
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' },
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
      <ScenarioSuiteEditor mode={mode} />
      <RouterProbe />
    </>
  )
}

function renderEditor(mode: 'create' | 'edit', name = 'nightly_suite') {
  const initialPath = mode === 'create'
    ? '/registry/scenario-suites/new'
    : `/registry/scenario-suites/${name}/edit`
  const router = createMemoryRouter([
    {
      path: '/registry/scenario-suites/new',
      element: editorElement('create'),
    },
    {
      path: '/registry/scenario-suites/:suiteName/edit',
      element: editorElement('edit'),
    },
    {
      path: '/registry/scenario-suites',
      element: <><h1>Suite library</h1><RouterProbe /></>,
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
  await user.click(screen.getByTestId('suite-preset-select'))
  await user.click(await screen.findByRole('option', { name: presetName }))
  await user.click(screen.getByTestId('add-suite-entry'))
}

describe('ScenarioSuiteEditor', () => {
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
    mockGenerateClientId.mockReset()
    mockListPresets.mockResolvedValue({ source: 'presets', items: AVAILABLE_PRESETS })
    mockGetSuite.mockResolvedValue(makeSuite())
    mockCreateSuite.mockResolvedValue(makeSuite())
    mockUpdateSuite.mockResolvedValue({ ...makeSuite(), version: 'suite-v2' })
    mockGenerateClientId.mockReturnValue(THIRD_ENTRY_ID)
  })

  it('creates duplicate preset references with distinct stable entry IDs', async () => {
    const user = userEvent.setup()
    mockGenerateClientId
      .mockReturnValueOnce(FIRST_ENTRY_ID)
      .mockReturnValueOnce(FIRST_ENTRY_ID)
      .mockReturnValueOnce(SECOND_ENTRY_ID)
    renderEditor('create')

    await user.type(await screen.findByTestId('suite-name-input'), 'nightly_suite')
    await addPreset(user, 'nightly_probe')
    await addPreset(user, 'nightly_probe')
    await user.click(screen.getByTestId('save-suite-btn'))

    await waitFor(() => expect(mockCreateSuite).toHaveBeenCalledWith({
      name: 'nightly_suite',
      schema_version: 1,
      entries: [
        { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        { entry_id: SECOND_ENTRY_ID, preset_name: 'nightly_probe' },
      ],
    }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent('/registry/scenario-suites')
  })

  it('shows projected status and saves dangling references in reordered entry order', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    expect(await screen.findByTestId('scenario-suite-editor')).toBeInTheDocument()
    expect(screen.getByText('Schema version 1 · Author: Ada Lovelace')).toBeInTheDocument()
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText("Preset 'retired_probe' is not available.")).toBeInTheDocument()
    expect(screen.getByText('Current preset version: preset-v1')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Move entry 2 up' }))
    await user.click(screen.getByTestId('save-suite-btn'))

    await waitFor(() => expect(mockUpdateSuite).toHaveBeenCalledWith(
      'nightly_suite',
      {
        name: 'nightly_suite',
        schema_version: 1,
        description: 'Nightly checks.',
        author: 'Ada Lovelace',
        entries: [
          { entry_id: SECOND_ENTRY_ID, preset_name: 'retired_probe' },
          { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
        ],
      },
      'suite-v1',
    ))
  })

  it('loads and saves an existing suite when the preset library is unavailable', async () => {
    const user = userEvent.setup()
    mockListPresets.mockRejectedValue(apiError(503, 'Preset storage is temporarily unavailable.'))
    renderEditor('edit')

    expect(await screen.findByTestId('scenario-suite-editor')).toBeInTheDocument()
    expect(screen.getByText(/Preset library unavailable/)).toHaveTextContent(
      'Existing entries can still be edited and saved, but new presets cannot be added.',
    )
    expect(screen.getByText("Preset 'retired_probe' is not available.")).toBeInTheDocument()
    expect(screen.getByText(/No presets are available to add/)).toBeInTheDocument()

    await user.type(screen.getByTestId('suite-description-input'), ' Updated.')
    await user.click(screen.getByTestId('save-suite-btn'))

    await waitFor(() => expect(mockUpdateSuite).toHaveBeenCalledWith(
      'nightly_suite',
      expect.objectContaining({ description: 'Nightly checks. Updated.' }),
      'suite-v1',
    ))
  })

  it('removes entries and adds a registered preset without changing existing IDs', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await screen.findByTestId('scenario-suite-editor')
    await user.click(screen.getByRole('button', { name: 'Remove entry 2' }))
    await addPreset(user, 'weekly_probe')
    await user.click(screen.getByTestId('save-suite-btn'))

    await waitFor(() => expect(mockUpdateSuite).toHaveBeenCalledWith(
      'nightly_suite',
      expect.objectContaining({
        entries: [
          { entry_id: FIRST_ENTRY_ID, preset_name: 'nightly_probe' },
          { entry_id: THIRD_ENTRY_ID, preset_name: 'weekly_probe' },
        ],
      }),
      'suite-v1',
    ))
  })

  it('reports a version conflict without navigating away', async () => {
    const user = userEvent.setup()
    mockUpdateSuite.mockRejectedValue(
      apiError(409, "Scenario suite 'nightly_suite' changed since it was read; re-read it and retry"),
    )
    renderEditor('edit')

    const description = await screen.findByTestId('suite-description-input')
    await user.type(description, ' Updated.')
    await user.click(screen.getByTestId('save-suite-btn'))

    expect(await screen.findByText(
      "Scenario suite 'nightly_suite' changed since it was read; re-read it and retry",
    )).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-suites/nightly_suite/edit',
    )
  })

  it('protects the draft while a save is in flight and after a version conflict', async () => {
    const user = userEvent.setup()
    let rejectSave: (reason: unknown) => void = () => {
      throw new Error('Save rejection was not initialized.')
    }
    mockUpdateSuite.mockImplementation(
      () => new Promise((_, reject) => {
        rejectSave = reject
      }),
    )
    renderEditor('edit')

    const description = await screen.findByTestId('suite-description-input')
    await user.type(description, ' Updated.')
    await user.click(screen.getByTestId('save-suite-btn'))
    expect(await screen.findByRole('button', { name: 'Saving...' })).toBeDisabled()

    const unloadEvent = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(unloadEvent)
    expect(unloadEvent.defaultPrevented).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Leave editor' }))
    expect(await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-suites/nightly_suite/edit',
    )

    await act(async () => {
      rejectSave(apiError(409, "Scenario suite 'nightly_suite' changed since it was read; re-read it and retry"))
    })
    expect(await screen.findByText(
      "Scenario suite 'nightly_suite' changed since it was read; re-read it and retry",
    )).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(description).toHaveValue('Nightly checks. Updated.')
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-suites/nightly_suite/edit',
    )
  })

  it('blocks in-app navigation until unsaved edits are discarded', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await user.type(await screen.findByTestId('suite-description-input'), ' Updated.')
    await user.click(await screen.findByRole('button', { name: 'Leave editor' }))

    expect(await screen.findByRole('dialog', { name: 'Discard unsaved changes?' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Keep editing' }))
    expect(screen.getByLabelText('Current URL')).toHaveTextContent(
      '/registry/scenario-suites/nightly_suite/edit',
    )

    await user.click(await screen.findByRole('button', { name: 'Leave editor' }))
    await user.click(await screen.findByRole('button', { name: 'Discard changes' }))
    expect(await screen.findByRole('heading', { name: 'Other page' })).toBeInTheDocument()
  })

  it('requests browser confirmation before unloading unsaved edits', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await user.type(await screen.findByTestId('suite-description-input'), ' Updated.')
    const event = new Event('beforeunload', { cancelable: true })
    window.dispatchEvent(event)

    expect(event.defaultPrevented).toBe(true)
  })

  it('leaves a clean editor without a discard prompt', async () => {
    const user = userEvent.setup()
    renderEditor('edit')

    await screen.findByTestId('scenario-suite-editor')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(await screen.findByRole('heading', { name: 'Suite library' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Discard unsaved changes?' })).not.toBeInTheDocument()
  })

  it('rejects an invalid name without calling the API', async () => {
    const user = userEvent.setup()
    renderEditor('create')

    await user.type(await screen.findByTestId('suite-name-input'), 'Nightly-Suite')
    await user.click(screen.getByTestId('save-suite-btn'))

    expect(await screen.findByText(/Use lowercase letters/)).toBeInTheDocument()
    expect(mockCreateSuite).not.toHaveBeenCalled()
  })

  it('reports a missing suite and returns to the library', async () => {
    const user = userEvent.setup()
    mockGetSuite.mockRejectedValue(apiError(404, 'No such suite.'))
    renderEditor('edit')

    expect(await screen.findByTestId('suite-editor-error-state')).toHaveTextContent(
      'No suite named "nightly_suite".',
    )
    await user.click(screen.getByRole('button', { name: 'Back to suites' }))
    expect(await screen.findByRole('heading', { name: 'Suite library' })).toBeInTheDocument()
  })
})
