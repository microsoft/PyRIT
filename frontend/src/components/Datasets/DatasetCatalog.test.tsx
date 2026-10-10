import { useState } from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'

import { apiClient } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { DATASET_DETAIL_PATH, datasetDetailPath } from '@/utils/routeParams'

import DatasetCatalog from './DatasetCatalog'
import DatasetSelection from './DatasetSelection'

let mockGet: jest.SpyInstance

function mockCatalog(items: DatasetInfo[]): void {
  mockGet.mockResolvedValue({ data: { items } })
}

function expectOnlyLoadedDatasetGets(): void {
  expect(mockGet.mock.calls.length).toBeGreaterThan(0)
  for (const call of mockGet.mock.calls) {
    expect(call[0]).toBe('/datasets')
    expect(call[1]).toEqual(expect.objectContaining({
      params: { loaded_only: true },
    }))
  }
}

function makeDataset(
  overrides: Partial<DatasetInfo> & Pick<DatasetInfo, 'name' | 'selection_key'>,
): DatasetInfo {
  return {
    loaded: true,
    provider_available: false,
    logical_examples: 2,
    seed_pieces: 3,
    objectives: 1,
    modalities: ['text'],
    harm_categories: ['hate'],
    has_unlabeled_harm_categories: false,
    ...overrides,
  }
}

function Harness({ initialPath }: { initialPath: string }) {
  return (
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[initialPath]}>
        <RefreshableRoutes />
      </MemoryRouter>
    </FluentProvider>
  )
}

function RefreshableRoutes() {
  const [epoch, setEpoch] = useState(0)
  const location = useLocation()
  const navigate = useNavigate()
  return (
    <>
      <Routes key={epoch}>
        <Route path="/datasets" element={<DatasetCatalog />} />
        <Route path="/datasets/detail" element={<DatasetSelection />} />
      </Routes>
      <output aria-label="Current URL">{`${location.pathname}${location.search}${location.hash}`}</output>
      <button type="button" onClick={() => navigate(-1)}>History back</button>
      <button type="button" onClick={() => navigate(1)}>History forward</button>
      <button type="button" onClick={() => setEpoch((value) => value + 1)}>Refresh page</button>
    </>
  )
}

function currentUrl(): URL {
  return new URL(screen.getByLabelText('Current URL').textContent ?? '', 'http://localhost')
}

function countValue(card: HTMLElement, label: string): string {
  const term = within(card).getByText(label)
  return term.parentElement?.querySelector('dd')?.textContent ?? ''
}

const LOADED = makeDataset({
  name: 'harmbench',
  selection_key: 'dataset:named:harmbench',
  provider_available: true,
  logical_examples: 1250000,
  seed_pieces: 1400000,
  objectives: 12,
  modalities: ['text', 'image_path'],
  harm_categories: ['hate'],
  has_unlabeled_harm_categories: true,
})

const UNNAMED = makeDataset({
  name: '(unnamed)',
  selection_key: 'dataset:unnamed',
  logical_examples: 4,
  seed_pieces: 4,
  objectives: 0,
  modalities: [],
  harm_categories: [],
  has_unlabeled_harm_categories: true,
})

const NAMED_UNNAMED = makeDataset({
  name: '__unnamed__',
  selection_key: 'dataset:named:__unnamed__',
  logical_examples: 1,
  seed_pieces: 1,
  objectives: null,
  modalities: ['audio_path'],
  harm_categories: [],
  has_unlabeled_harm_categories: false,
})

describe('DatasetCatalog', () => {
  beforeEach(() => {
    mockGet = jest.spyOn(apiClient, 'get')
    mockCatalog([LOADED, UNNAMED, NAMED_UNNAMED])
  })

  afterEach(() => {
    expectOnlyLoadedDatasetGets()
    jest.restoreAllMocks()
  })

  it('shows a loading state while the catalog request is in flight', () => {
    mockGet.mockReturnValue(new Promise(() => {}))
    render(<Harness initialPath="/datasets" />)
    expect(screen.getByText('Loading datasets...')).toBeInTheDocument()
  })

  it('renders summary cards from the catalog response', async () => {
    render(<Harness initialPath="/datasets" />)

    const loaded = await screen.findByRole('article', { name: 'harmbench' })
    expect(screen.getByRole('heading', { name: 'Datasets' })).toBeInTheDocument()
    expect(within(loaded).getByRole('link', { name: 'harmbench' })).toHaveAttribute(
      'href',
      datasetDetailPath('dataset:named:harmbench'),
    )
    expect(within(loaded).queryByText('Loaded')).not.toBeInTheDocument()
    expect(within(loaded).queryByText('Not loaded')).not.toBeInTheDocument()
    expect(within(loaded).queryByText('Provider available')).not.toBeInTheDocument()
    expect(countValue(loaded, 'Logical examples')).toBe('1,250,000')
    expect(countValue(loaded, 'Seed pieces')).toBe('1,400,000')
    expect(countValue(loaded, 'Objectives')).toBe('12')
    expect(within(loaded).getByText('text')).toBeInTheDocument()
    expect(within(loaded).getByText('image_path')).toBeInTheDocument()
    expect(within(loaded).getByText('hate')).toBeInTheDocument()
    expect(within(loaded).getByText('Not labeled')).toBeInTheDocument()
    expect(within(loaded).queryByText('Safe')).not.toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /download|load dataset/i })).not.toBeInTheDocument()

    const unnamed = screen.getByRole('article', { name: '(unnamed)' })
    expect(within(unnamed).getByRole('link', { name: '(unnamed)' })).toHaveAttribute(
      'href',
      datasetDetailPath('dataset:unnamed'),
    )
    expect(within(unnamed).queryByRole('link', { name: '__unnamed__' })).not.toBeInTheDocument()
    expect(countValue(unnamed, 'Objectives')).toBe('0')
    expect(within(unnamed).getByText('None')).toBeInTheDocument()
    expect(within(unnamed).getByText('Not labeled')).toBeInTheDocument()

    const literal = screen.getByRole('article', { name: '__unnamed__' })
    expect(within(literal).getByRole('link', { name: '__unnamed__' })).toHaveAttribute(
      'href',
      datasetDetailPath('dataset:named:__unnamed__'),
    )
    expect(countValue(literal, 'Objectives')).toBe('Unknown')
    expect(within(literal).getByText('None')).toBeInTheDocument()
  })

  it('shows an empty state when memory has no datasets', async () => {
    mockCatalog([])
    render(<Harness initialPath="/datasets" />)

    expect(await screen.findByText('No datasets in memory')).toBeInTheDocument()
    expect(screen.getByText(/does not download them/i)).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /harmbench|gated/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /load|download/i })).not.toBeInTheDocument()
  })

  it('shows an error and retries the catalog request', async () => {
    const user = userEvent.setup()
    mockGet.mockRejectedValueOnce(new Error('Catalog unavailable'))
    render(<Harness initialPath="/datasets" />)

    expect(await screen.findByText('Catalog unavailable')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus())

    mockGet.mockResolvedValueOnce({ data: { items: [LOADED] } })
    await user.click(screen.getByRole('button', { name: 'Retry' }))

    expect(await screen.findByRole('link', { name: 'harmbench' })).toBeInTheDocument()
    expect(mockGet).toHaveBeenCalledTimes(2)
  })

  it('filters by name and reports no matches without rendering the query as HTML', async () => {
    const user = userEvent.setup()
    render(<Harness initialPath="/datasets" />)
    const search = await screen.findByRole('textbox', { name: 'Search datasets' })

    await user.type(search, '<img src=x onerror=alert(1)>')

    expect(await screen.findByText(/No datasets match "<img src=x onerror=alert\(1\)>"/)).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(currentUrl().searchParams.get('q')).toBe('<img src=x onerror=alert(1)>')

    await user.clear(search)
    await user.type(search, 'zzz')
    expect(await screen.findByText('No datasets match "zzz"')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(await screen.findByRole('link', { name: 'harmbench' })).toBeInTheDocument()
    expect(currentUrl().search).toBe('')
  })

  it('shows the no-match copy when only facets exclude every dataset', async () => {
    render(<Harness initialPath="/datasets?modality=missing" />)
    expect(await screen.findByText('No datasets match these filters')).toBeInTheDocument()
  })

  it('updates the URL from the sort, modality, and harm controls', async () => {
    const user = userEvent.setup()
    render(<Harness initialPath="/datasets" />)
    await screen.findByRole('link', { name: 'harmbench' })

    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort datasets' }), 'logical_examples_desc')
    expect(currentUrl().searchParams.get('sort')).toBe('logical_examples_desc')

    await user.click(screen.getByTestId('dataset-modality-filter'))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'audio_path' }))
    expect(currentUrl().searchParams.get('modality')).toBe('audio_path')
    expect(screen.getByRole('link', { name: '__unnamed__' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Clear filters' }))
    await user.click(screen.getByTestId('dataset-harm-filter'))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'hate' }))
    expect(currentUrl().searchParams.get('harm_category')).toBe('hate')
    expect(screen.getByRole('link', { name: 'harmbench' })).toBeInTheDocument()

    await user.click(screen.getByRole('checkbox', { name: 'Not labeled' }))
    expect(currentUrl().searchParams.get('unlabeled')).toBe('true')
    expect(screen.getByRole('link', { name: '(unnamed)' })).toBeInTheDocument()
  })

  it('reaches search, clear, and the dataset link from the keyboard', async () => {
    const user = userEvent.setup()
    render(<Harness initialPath="/datasets" />)
    const search = await screen.findByRole('textbox', { name: 'Search datasets' })

    for (let step = 0; step < 8 && document.activeElement !== search; step += 1) {
      await user.tab()
    }
    expect(search).toHaveFocus()
    await user.keyboard('zzz')

    const clear = await screen.findByRole('button', { name: 'Clear filters' })
    for (let step = 0; step < 12 && document.activeElement !== clear; step += 1) {
      await user.tab()
    }
    expect(clear).toHaveFocus()
    await user.keyboard('{Enter}')

    const link = await screen.findByRole('link', { name: 'harmbench' })
    for (let step = 0; step < 15 && document.activeElement !== link; step += 1) {
      await user.tab()
    }
    expect(link).toHaveFocus()
    await user.keyboard('{Enter}')

    expect(await screen.findByRole('heading', { level: 1, name: 'harmbench' })).toBeInTheDocument()
    expect(currentUrl().pathname).toBe(DATASET_DETAIL_PATH)
    expect(currentUrl().searchParams.get('selection_key')).toBe('dataset:named:harmbench')
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
  })

  it('ignores a catalog response that arrives after the page is left', async () => {
    let resolveLoad: (value: { data: { items: DatasetInfo[] } }) => void = () => {}
    mockGet.mockReturnValue(new Promise((resolve) => {
      resolveLoad = resolve
    }))
    const { unmount } = render(<Harness initialPath="/datasets" />)
    unmount()
    await act(async () => {
      resolveLoad({ data: { items: [LOADED] } })
    })
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()
  })
})

function catalogUrl(): string {
  const params = new URLSearchParams()
  params.set('q', 'データ')
  params.append('modality', 'image/png')
  params.append('harm_category', 'a&b')
  params.set('unlabeled', 'true')
  params.set('sort', 'logical_examples_desc')
  return `/datasets?${params.toString()}`
}

describe('DatasetCatalog URL state', () => {
  beforeEach(() => {
    mockGet = jest.spyOn(apiClient, 'get')
    mockCatalog([
        makeDataset({
          name: 'データ set',
          selection_key: 'dataset:named:データ set',
          modalities: ['image/png'],
          harm_categories: ['a&b'],
          has_unlabeled_harm_categories: true,
          objectives: 9,
        }),
        makeDataset({
          name: 'other',
          selection_key: 'dataset:named:other',
          modalities: ['text'],
          harm_categories: ['violence'],
          objectives: 1,
        }),
      ])
  })

  afterEach(() => {
    expectOnlyLoadedDatasetGets()
    jest.restoreAllMocks()
  })

  it('keeps search, facets, and sort in the URL across refresh, back, and forward', async () => {
    const user = userEvent.setup()
    render(<Harness initialPath={catalogUrl()} />)

    expect(await screen.findByRole('link', { name: 'データ set' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'other' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Search datasets' })).toHaveValue('データ')
    expect(screen.getByRole('combobox', { name: 'Sort datasets' })).toHaveValue('logical_examples_desc')
    expect(screen.getByRole('checkbox', { name: 'Not labeled' })).toBeChecked()
    expect(screen.getByTestId('dataset-modality-filter')).toHaveValue('Modalities: image/png')
    expect(screen.getByTestId('dataset-harm-filter')).toHaveValue('Harm categories: a&b')

    await user.click(screen.getByRole('button', { name: 'Refresh page' }))
    expect(await screen.findByRole('link', { name: 'データ set' })).toBeInTheDocument()
    expect(currentUrl().searchParams.get('q')).toBe('データ')
    expect(currentUrl().searchParams.getAll('modality')).toEqual(['image/png'])
    expect(currentUrl().searchParams.getAll('harm_category')).toEqual(['a&b'])
    expect(currentUrl().searchParams.get('unlabeled')).toBe('true')
    expect(currentUrl().searchParams.get('sort')).toBe('logical_examples_desc')

    await user.click(screen.getByRole('link', { name: 'データ set' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'データ set' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'History back' }))

    expect(await screen.findByRole('link', { name: 'データ set' })).toBeInTheDocument()
    expect(currentUrl().pathname).toBe('/datasets')
    expect(currentUrl().searchParams.get('q')).toBe('データ')
    expect(currentUrl().searchParams.get('harm_category')).toBe('a&b')
    expect(screen.getByRole('textbox', { name: 'Search datasets' })).toHaveValue('データ')

    await user.click(screen.getByRole('button', { name: 'History forward' }))
    expect(await screen.findByRole('heading', { level: 1, name: 'データ set' })).toBeInTheDocument()
    expect(currentUrl().pathname).toBe(DATASET_DETAIL_PATH)
    expect(currentUrl().searchParams.get('selection_key')).toBe('dataset:named:データ set')
  })
})

const SELECTION_CASES: Array<[string, string, string]> = [
  ['unnamed population', 'dataset:unnamed', '(unnamed)'],
  ['literal __unnamed__ name', 'dataset:named:__unnamed__', '__unnamed__'],
  ['spaces', 'dataset:named:my dataset', 'my dataset'],
  ['slash', 'dataset:named:a/b', 'a/b'],
  ['question mark', 'dataset:named:a?b', 'a?b'],
  ['hash', 'dataset:named:a#b', 'a#b'],
  ['ampersand', 'dataset:named:a&b', 'a&b'],
  ['percent', 'dataset:named:100%', '100%'],
  ['unicode', 'dataset:named:データ', 'データ'],
]

describe('Dataset selection keys', () => {
  beforeEach(() => {
    mockGet = jest.spyOn(apiClient, 'get')
  })

  afterEach(() => {
    expectOnlyLoadedDatasetGets()
    jest.restoreAllMocks()
  })

  it.each(SELECTION_CASES)(
    'round-trips the %s key through navigation, refresh, and back',
    async (_label, selectionKey, displayName) => {
      const user = userEvent.setup()
      mockCatalog([makeDataset({
        name: displayName,
        selection_key: selectionKey,
        modalities: [],
        harm_categories: [],
      })])

      render(<Harness initialPath="/datasets" />)
      const link = await screen.findByRole('link', { name: displayName })
      const href = link.getAttribute('href') ?? ''
      const hrefUrl = new URL(href, 'http://localhost')
      expect(hrefUrl.pathname).toBe(DATASET_DETAIL_PATH)
      expect(href.split('?')[0]).toBe(DATASET_DETAIL_PATH)
      expect(hrefUrl.hash).toBe('')
      expect(Array.from(hrefUrl.searchParams.keys())).toEqual(['selection_key'])
      expect(hrefUrl.searchParams.get('selection_key')).toBe(selectionKey)

      await user.click(link)
      expect(await screen.findByRole('heading', { level: 1, name: displayName })).toBeInTheDocument()
      expect(currentUrl().pathname).toBe(DATASET_DETAIL_PATH)
      expect(currentUrl().hash).toBe('')
      expect(currentUrl().searchParams.get('selection_key')).toBe(selectionKey)
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
      expect(screen.getByText('Individual examples are not listed in this view.')).toBeInTheDocument()

      await user.click(screen.getByRole('button', { name: 'Refresh page' }))
      expect(await screen.findByRole('heading', { level: 1, name: displayName })).toBeInTheDocument()
      expect(currentUrl().searchParams.get('selection_key')).toBe(selectionKey)
      expect(currentUrl().hash).toBe('')

      await user.click(screen.getByRole('button', { name: 'History back' }))
      const returned = await screen.findByRole('link', { name: displayName })
      expect(currentUrl().pathname).toBe('/datasets')
      expect(new URL(returned.getAttribute('href') ?? '', 'http://localhost').searchParams.get('selection_key'))
        .toBe(selectionKey)
    },
  )
})
