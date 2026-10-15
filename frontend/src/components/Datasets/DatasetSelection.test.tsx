import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { apiClient } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { datasetDetailPath } from '@/utils/routeParams'

import DatasetSelection from './DatasetSelection'

let generation = ''
let ready = true

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ ready, state: ready ? 'ready' : 'connecting', generation }),
}))

let mockGet: jest.SpyInstance

interface TrackedCall {
  url: string
  params: Record<string, unknown>
  generation: string
}

const EMPTY_SEED_PAGE = {
  items: [],
  pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: null },
  total: 0,
}

function expectOnlyLoadedDatasetGets(): void {
  const catalogCalls = mockGet.mock.calls.filter((call) => call[0] === '/datasets')
  expect(catalogCalls.length).toBeGreaterThan(0)
  for (const call of catalogCalls) {
    expect(call[1]).toEqual(expect.objectContaining({
      params: { loaded_only: true },
    }))
  }
  for (const call of mockGet.mock.calls) {
    const url = String(call[0])
    if (url === '/datasets') continue
    expect(url === '/datasets/seeds' || url.startsWith('/datasets/seeds/')).toBe(true)
    const params = (call[1] as { params?: Record<string, unknown> } | undefined)?.params
    expect(typeof params?.selection_key).toBe('string')
    expect(params?.selection_key).not.toBe('')
    expect(params).not.toHaveProperty('q')
    expect(params).not.toHaveProperty('sort')
    expect(params).not.toHaveProperty('unlabeled')
  }
}

function seedCalls(): unknown[][] {
  return mockGet.mock.calls.filter((call) => String(call[0]).startsWith('/datasets/seeds'))
}

function makeDataset(
  overrides: Partial<DatasetInfo> & Pick<DatasetInfo, 'name' | 'selection_key'>,
): DatasetInfo {
  return {
    loaded: true,
    provider_available: true,
    logical_examples: 2,
    seed_pieces: 2,
    objectives: 2,
    modalities: ['text'],
    harm_categories: ['hate'],
    has_unlabeled_harm_categories: false,
    ...overrides,
  }
}

function LocationReadout() {
  const location = useLocation()
  return <output aria-label="Current URL">{`${location.pathname}${location.search}`}</output>
}

function SelectionHarness({
  pathname,
  search,
  state,
}: {
  pathname: string
  search: string
  state?: unknown
}) {
  const [runtimeTick, setRuntimeTick] = useState(0)
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[{ pathname, search, state }]}>
        <Routes>
          <Route path="/datasets" element={<p>Catalog</p>} />
          <Route path="/datasets/detail" element={<DatasetSelection />} />
        </Routes>
        <LocationReadout />
      </MemoryRouter>
      <button type="button" onClick={() => setRuntimeTick((value) => value + 1)}>Apply runtime</button>
      <span data-testid="runtime-tick">{runtimeTick}</span>
    </FluentProvider>
  )
}

function renderSelection(initialPath: string, state?: unknown) {
  const url = new URL(initialPath, 'http://localhost')
  return render(
    <SelectionHarness pathname={url.pathname} search={url.search} state={state} />,
  )
}

function currentUrl(): URL {
  return new URL(screen.getByLabelText('Current URL').textContent ?? '', 'http://localhost')
}

const LOADED = makeDataset({
  name: 'harmbench',
  selection_key: 'dataset:named:harmbench',
})

describe('DatasetSelection', () => {
  beforeEach(() => {
    generation = ''
    ready = true
    mockGet = jest.spyOn(apiClient, 'get').mockImplementation((url: string) => {
      if (url === '/datasets/seeds' || url.startsWith('/datasets/seeds/')) {
        return Promise.resolve({ data: EMPTY_SEED_PAGE })
      }
      return Promise.resolve({ data: { items: [LOADED] } })
    })
  })

  afterEach(() => {
    expectOnlyLoadedDatasetGets()
    jest.restoreAllMocks()
  })

  it('focuses the dataset name when the selection key matches the catalog', async () => {
    renderSelection(datasetDetailPath(LOADED.selection_key))

    const heading = await screen.findByRole('heading', { level: 1, name: 'harmbench' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(await screen.findByText('No examples in this dataset')).toBeInTheDocument()
    expect(screen.queryByText(/Individual examples are not yet listed/)).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    await waitFor(() => expect(seedCalls().length).toBeGreaterThan(0))
    expect(seedCalls()[0]?.[1]).toEqual(expect.objectContaining({
      params: expect.objectContaining({ selection_key: LOADED.selection_key }),
    }))
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()
    const summary = screen.getByRole('article', { name: 'harmbench' })
    expect(within(summary).queryByRole('heading')).not.toBeInTheDocument()
  })

  it('shows not-found for an unknown key and renders that key as text', async () => {
    const key = '<img src=x onerror=alert(1)>'
    renderSelection(datasetDetailPath(key))

    const heading = await screen.findByRole('heading', { name: 'Dataset not found' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(screen.getByText(key)).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('script')).toBeNull()
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()
    expect(seedCalls()).toEqual([])
  })

  it('truncates a long unknown selection key and renders it as text', async () => {
    const key = `<img src=x onerror=alert(1)>${'a'.repeat(200)}`
    renderSelection(datasetDetailPath(key))

    const shown = `${key.slice(0, 79)}…`
    expect(await screen.findByText(shown)).toBeInTheDocument()
    expect(screen.queryByText(key)).not.toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('script')).toBeNull()
    expect(seedCalls()).toEqual([])
  })

  it('does not choose between repeated keys or fetch them', async () => {
    renderSelection('/datasets/detail?selection_key=dataset:named:harmbench&selection_key=dataset:named:other')

    expect(await screen.findByRole('heading', { name: 'Dataset not found' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'harmbench' })).not.toBeInTheDocument()
    expect(screen.queryByText('dataset:named:harmbench')).not.toBeInTheDocument()
    expect(seedCalls()).toEqual([])
  })

  it('shows not-found when the selection key is missing', async () => {
    renderSelection('/datasets/detail')

    expect(await screen.findByRole('heading', { name: 'Dataset not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to datasets' })).toHaveAttribute('href', '/datasets')
    expect(seedCalls()).toEqual([])
  })

  it('focuses retry after a catalog error and can load again', async () => {
    const user = userEvent.setup()
    mockGet.mockRejectedValueOnce(new Error('Catalog unavailable'))
    renderSelection(datasetDetailPath(LOADED.selection_key))

    expect(await screen.findByText('Catalog unavailable')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus())

    mockGet.mockResolvedValueOnce({ data: { items: [LOADED] } })
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'harmbench' })).toBeInTheDocument()
  })

  it('returns to the catalog from the back link', async () => {
    const user = userEvent.setup()
    renderSelection(datasetDetailPath(LOADED.selection_key))
    await screen.findByRole('heading', { level: 1, name: 'harmbench' })

    expect(screen.getByRole('link', { name: 'Back to datasets' })).toHaveAttribute('href', '/datasets')
    await user.click(screen.getByRole('link', { name: 'Back to datasets' }))
    expect(await screen.findByText('Catalog')).toBeInTheDocument()
  })

  it('ignores a return path that is not the datasets catalog', async () => {
    renderSelection(datasetDetailPath(LOADED.selection_key), {
      catalogPath: 'https://evil.example/datasets?q=policy',
    })

    expect(await screen.findByRole('link', { name: 'Back to datasets' })).toHaveAttribute('href', '/datasets')
  })

  it('does not send catalog name search or sort to the seed API', async () => {
    renderSelection(`${datasetDetailPath(LOADED.selection_key)}&q=policy&sort=name&unlabeled=true`)

    await screen.findByRole('heading', { level: 1, name: 'harmbench' })
    await waitFor(() => expect(seedCalls().length).toBeGreaterThan(0))
    const params = (seedCalls()[0]?.[1] as { params: Record<string, unknown> }).params
    expect(params.selection_key).toBe(LOADED.selection_key)
    expect(params).not.toHaveProperty('q')
    expect(params).not.toHaveProperty('sort')
    expect(params).not.toHaveProperty('unlabeled')
    expect(params).not.toHaveProperty('search')
    expect(screen.getByRole('textbox', { name: 'Search examples' })).toHaveValue('')
  })

  it('does not send a direct-link page to the next runtime', async () => {
    const user = userEvent.setup()
    const calls: TrackedCall[] = []
    generation = 'gen-1'
    ready = true
    mockLinkedExample(calls)
    renderSelection(`${datasetDetailPath(LOADED.selection_key)}&cursor=cursor-2&example=example-2`)

    await waitFor(() => {
      expect(trackedLists(calls, 'gen-1').some((call) => call.params.cursor === 'cursor-2')).toBe(true)
      expect(trackedDetails(calls, 'gen-1').some((call) => call.url.endsWith('/example-2'))).toBe(true)
    })
    expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
    expect(currentUrl().searchParams.get('example')).toBe('example-2')

    generation = 'gen-2'
    await user.click(screen.getByRole('button', { name: 'Apply runtime' }))

    await waitFor(() => expect(trackedLists(calls, 'gen-2').length).toBeGreaterThan(0))
    expect(trackedLists(calls, 'gen-2').every((call) => call.params.cursor == null)).toBe(true)
    expect(trackedDetails(calls, 'gen-2')).toEqual([])
    await waitFor(() => {
      expect(currentUrl().searchParams.get('cursor')).toBeNull()
      expect(currentUrl().searchParams.get('example')).toBeNull()
    })
  })

  it('keeps a direct-link page when the runtime first becomes known', async () => {
    const user = userEvent.setup()
    const calls: TrackedCall[] = []
    generation = ''
    ready = false
    mockLinkedExample(calls)
    renderSelection(`${datasetDetailPath(LOADED.selection_key)}&cursor=cursor-2&example=example-2`)

    await waitFor(() => {
      expect(trackedLists(calls, '').some((call) => call.params.cursor === 'cursor-2')).toBe(true)
      expect(trackedDetails(calls, '').some((call) => call.url.endsWith('/example-2'))).toBe(true)
    })

    generation = 'gen-1'
    ready = true
    await user.click(screen.getByRole('button', { name: 'Apply runtime' }))

    await waitFor(() => expect(trackedLists(calls, 'gen-1').length).toBeGreaterThan(0))
    expect(trackedLists(calls, 'gen-1').every((call) => call.params.cursor === 'cursor-2')).toBe(true)
    expect(trackedDetails(calls, 'gen-1').some((call) => call.url.endsWith('/example-2'))).toBe(true)
    expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
    expect(currentUrl().searchParams.get('example')).toBe('example-2')
  })
})

const LINKED_EXAMPLE = {
  example_id: 'example-2',
  name: 'beta',
  preview: 'later preview',
  preview_truncated: false,
  modalities: ['text'],
  seed_types: ['prompt'],
  piece_count: 1,
  objective_count: 0,
  harm_categories: ['hate'],
  has_unlabeled_harm: false,
}

function mockLinkedExample(calls: TrackedCall[]): void {
  mockGet.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
    const requestUrl = String(url)
    if (requestUrl === '/datasets' || requestUrl === '/datasets/seeds' || requestUrl.startsWith('/datasets/seeds/')) {
      calls.push({ url: requestUrl, params: config?.params ?? {}, generation })
    }
    if (requestUrl === '/datasets/seeds') {
      return Promise.resolve({
        data: {
          items: [LINKED_EXAMPLE],
          pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: null },
          total: 1,
        },
      })
    }
    if (requestUrl.startsWith('/datasets/seeds/')) {
      return Promise.resolve({ data: { ...LINKED_EXAMPLE, members: [] } })
    }
    return Promise.resolve({ data: { items: [LOADED] } })
  })
}

function trackedLists(calls: readonly TrackedCall[], runtimeGeneration: string): TrackedCall[] {
  return calls.filter((call) => call.generation === runtimeGeneration && call.url === '/datasets/seeds')
}

function trackedDetails(calls: readonly TrackedCall[], runtimeGeneration: string): TrackedCall[] {
  return calls.filter((call) => call.generation === runtimeGeneration && call.url.startsWith('/datasets/seeds/'))
}
