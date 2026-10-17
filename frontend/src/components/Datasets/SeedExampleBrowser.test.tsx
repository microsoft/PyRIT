import { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation, useNavigate } from 'react-router'

import { apiClient } from '@/services/api'
import type { DatasetInfo, SeedExampleDetailResponse, SeedExampleListResponse, SeedExampleSummary, SeedRecord } from '@/types'

import SeedExampleBrowser from './SeedExampleBrowser'

let generation = 'gen-1'
let ready = true

jest.mock('@/hooks/useRuntime', () => ({
  useRuntime: () => ({ ready, state: ready ? 'ready' : 'connecting', generation }),
}))

const onCatalogStale = jest.fn()
let mockGet: jest.SpyInstance

const DATASET: DatasetInfo = {
  name: 'harmbench',
  selection_key: 'dataset:named:harmbench',
  loaded: true,
  provider_available: true,
  logical_examples: 12,
  seed_pieces: 14,
  objectives: 2,
  modalities: ['text', 'image_path'],
  harm_categories: ['hate', 'cybersecurity_credentials_and_privileged_information_disclosure'],
  has_unlabeled_harm_categories: false,
}

function summary(overrides: Partial<SeedExampleSummary> & Pick<SeedExampleSummary, 'example_id' | 'preview'>): SeedExampleSummary {
  return {
    name: null,
    preview_truncated: false,
    modalities: ['text'],
    seed_types: ['prompt'],
    piece_count: 1,
    objective_count: 0,
    harm_categories: ['hate'],
    has_unlabeled_harm: false,
    ...overrides,
  }
}

function member(overrides: Partial<SeedRecord> & Pick<SeedRecord, 'id' | 'value'>): SeedRecord {
  return {
    seed_type: 'prompt',
    value_sha256: null,
    data_type: 'text',
    name: null,
    dataset_name: 'harmbench',
    origin: 'unknown',
    harm_categories: null,
    description: null,
    authors: null,
    groups: null,
    source: null,
    date_added: '2026-01-01T00:00:00Z',
    added_by: 'tester',
    metadata: null,
    prompt_group_id: null,
    sequence: null,
    role: null,
    parameters: null,
    conditions: null,
    response_json_schema: null,
    ...overrides,
  }
}

const FIRST = summary({
  example_id: 'example-1',
  name: 'alpha',
  preview: 'short preview',
  modalities: ['text', 'image_path'],
  seed_types: ['prompt', 'objective'],
  piece_count: 2,
  objective_count: 1,
  harm_categories: ['cybersecurity_credentials_and_privileged_information_disclosure'],
  has_unlabeled_harm: true,
})
const SECOND = summary({ example_id: 'example-2', name: 'beta', preview: 'later preview' })
const DUPLICATE = summary({ example_id: 'example-1', name: 'alpha copy', preview: 'duplicate row' })

function listPage(items: SeedExampleSummary[], extras: Partial<SeedExampleListResponse> = {}): SeedExampleListResponse {
  return {
    items,
    pagination: {
      limit: 20,
      has_more: false,
      next_cursor: null,
      prev_cursor: 'echo-current',
      ...extras.pagination,
    },
    total: extras.total ?? 12,
  }
}

const DETAIL: SeedExampleDetailResponse = {
  ...FIRST,
  members: [
    member({ id: 'prompt-1', seed_type: 'prompt', value: 'prompt body\nkept', sequence: 2 }),
    member({ id: 'objective-1', seed_type: 'objective', value: 'objective body', data_type: 'text' }),
    member({ id: 'image-1', seed_type: 'prompt', value: 'photo.png', data_type: 'image_path' }),
  ],
}

function paramsOf(call: unknown[] | undefined): Record<string, unknown> {
  return (call?.[1] as { params?: Record<string, unknown> } | undefined)?.params ?? {}
}

function seedListCalls(): unknown[][] {
  return mockGet.mock.calls.filter((call) => call[0] === '/datasets/seeds')
}

function renderBrowser(search = '', state?: unknown) {
  const path = `/datasets/detail?selection_key=${encodeURIComponent(DATASET.selection_key)}${search}`
  const url = new URL(path, 'http://localhost')
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[{ pathname: url.pathname, search: url.search, state }]}>
        <Harness dataset={DATASET} />
      </MemoryRouter>
    </FluentProvider>,
  )
}

function Harness({ dataset }: { dataset: DatasetInfo }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [epoch, setEpoch] = useState(0)
  const [runtimeTick, setRuntimeTick] = useState(0)
  return (
    <>
      <Routes key={epoch}>
        <Route path="/datasets/detail" element={<SeedExampleBrowser dataset={dataset} onCatalogStale={onCatalogStale} />} />
      </Routes>
      <output aria-label="Current URL">{`${location.pathname}${location.search}`}</output>
      <output aria-label="Catalog return">{
        location.state && typeof location.state === 'object' && 'catalogPath' in location.state
          ? String((location.state as { catalogPath?: unknown }).catalogPath)
          : ''
      }</output>
      <button type="button" onClick={() => navigate(-1)}>History back</button>
      <button type="button" onClick={() => setEpoch((value) => value + 1)}>Refresh page</button>
      <button type="button" onClick={() => setRuntimeTick((value) => value + 1)}>Apply runtime</button>
      <span data-testid="runtime-tick">{runtimeTick}</span>
    </>
  )
}

function currentUrl(): URL {
  return new URL(screen.getByLabelText('Current URL').textContent ?? '', 'http://localhost')
}

function exampleStatus(): HTMLElement {
  return screen.getByTestId('example-status')
}

describe('SeedExampleBrowser', () => {
  beforeEach(() => {
    generation = 'gen-1'
    ready = true
    onCatalogStale.mockReset()
    mockGet = jest.spyOn(apiClient, 'get').mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
      if (url === '/datasets/seeds') {
        const params = config?.params ?? {}
        if (params.cursor === 'bad') {
          return Promise.reject({
            isAxiosError: true,
            response: { status: 400, data: { detail: 'The cursor is not valid for this dataset and these filters' } },
          })
        }
        if (params.cursor === 'cursor-2') {
          return Promise.resolve({ data: listPage([SECOND], { pagination: { limit: 20, has_more: false, next_cursor: null, prev_cursor: 'cursor-2' } }) })
        }
        if (params.search === 'missing') {
          return Promise.resolve({ data: listPage([], { total: 0 }) })
        }
        if (params.search === 'password') {
          return Promise.resolve({
            data: listPage([summary({
              example_id: 'config-1',
              preview: '[simulated_conversation]',
              seed_types: ['simulated_conversation'],
            })]),
          })
        }
        return Promise.resolve({
          data: listPage([FIRST, SECOND], {
            pagination: { limit: 20, has_more: true, next_cursor: 'cursor-2', prev_cursor: null },
          }),
        })
      }
      if (String(url).startsWith('/datasets/seeds/')) {
        return Promise.resolve({ data: DETAIL })
      }
      return Promise.reject(new Error(`Unexpected ${String(url)}`))
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('shows the server total and keeps multimodal examples in one row', async () => {
    renderBrowser()

    await waitFor(() => expect(screen.getByTestId('example-status')).toHaveTextContent('12 matching examples'))
    expect(document.querySelectorAll('[role="status"]')).toHaveLength(1)
    const table = screen.getByRole('table', { name: 'Examples' })
    const rows = within(table).getAllByRole('row')
    expect(rows).toHaveLength(3)
    expect(within(table).getByText('alpha')).toBeInTheDocument()
    expect(within(table).getByText('beta')).toBeInTheDocument()
    expect(within(table).getAllByText('text').length).toBeGreaterThan(0)
    expect(within(table).getAllByText('image_path').length).toBeGreaterThan(0)
    expect(within(table).getByText('cybersecurity_credentials_and_privileged_information_disclosure')).toBeInTheDocument()
    expect(within(table).getByText('Not labeled')).toBeInTheDocument()
    expect(screen.queryByText('Safe')).not.toBeInTheDocument()
    expect(screen.getByText(/Simulated-conversation JSON is not included/)).toBeInTheDocument()
    expect(paramsOf(seedListCalls()[0])).toEqual(expect.objectContaining({
      selection_key: DATASET.selection_key,
      limit: 20,
    }))
    expect(paramsOf(seedListCalls()[0])).not.toHaveProperty('q')
  })

  it('keeps one live status for matches and no matches', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await waitFor(() => expect(screen.getByTestId('example-status')).toHaveTextContent('12 matching examples'))
    const status = screen.getByTestId('example-status')

    await user.type(screen.getByRole('textbox', { name: 'Search examples' }), 'missing')

    await waitFor(() => expect(status).toHaveTextContent('No matching examples'))
    expect(exampleStatus()).toBe(status)
    expect(screen.getByText('Clear the search or filters to see every example in this dataset.')).toBeInTheDocument()
    const last = seedListCalls()[seedListCalls().length - 1]
    expect(paramsOf(last).search).toBe('missing')
    expect(paramsOf(last)).not.toHaveProperty('q')
  })

  it('sends seed-type filtering without searching simulated-conversation JSON locally', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await waitFor(() => expect(screen.getByTestId('example-status')).toHaveTextContent('12 matching examples'))

    await user.click(screen.getByTestId('example-seed-type-filter'))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Simulated conversation' }))

    await waitFor(() => {
      const last = seedListCalls()[seedListCalls().length - 1]
      expect(paramsOf(last).seed_type).toEqual(['simulated_conversation'])
    })
    expect(paramsOf(seedListCalls()[seedListCalls().length - 1])).not.toHaveProperty('search')

    await user.type(screen.getByRole('textbox', { name: 'Search examples' }), 'password')
    expect(await screen.findByText('[simulated_conversation]')).toBeInTheDocument()
  })

  it('pages with visited cursors and does not treat prev_cursor as the previous page', async () => {
    const user = userEvent.setup()
    renderBrowser('', { catalogPath: '/datasets?q=policy&modality=text&sort=logical_examples_desc' })
    await screen.findByText('alpha')

    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('later preview')).toBeInTheDocument()
    expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(await screen.findByText('alpha')).toBeInTheDocument()
    expect(currentUrl().searchParams.get('cursor')).toBeNull()
    const cursors = seedListCalls().map((call) => paramsOf(call).cursor)
    expect(cursors).not.toContain('echo-current')
    expect(screen.getByLabelText('Catalog return')).toHaveTextContent('/datasets?q=policy&modality=text&sort=logical_examples_desc')
  })

  it('resets the cursor when the page size changes and preserves filters on refresh', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('alpha')
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await screen.findByText('later preview')

    await user.selectOptions(screen.getByRole('combobox', { name: 'Examples per page' }), '50')
    await waitFor(() => expect(currentUrl().searchParams.get('cursor')).toBeNull())
    expect(currentUrl().searchParams.get('limit')).toBe('50')
    expect(paramsOf(seedListCalls()[seedListCalls().length - 1]).cursor).toBeUndefined()

    await user.click(screen.getByRole('button', { name: 'Refresh page' }))
    expect(await screen.findByRole('combobox', { name: 'Examples per page' })).toHaveValue('50')
    expect(currentUrl().searchParams.get('selection_key')).toBe(DATASET.selection_key)
  })

  it('loads the inspected example for this dataset only and moves focus into the detail', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('alpha')

    await user.click(screen.getByRole('button', { name: 'Show alpha' }))

    expect(await screen.findByText('prompt body', { exact: false })).toBeInTheDocument()
    const members = screen.getAllByRole('article')
    expect(members[0]).toHaveAccessibleName('Member 1')
    expect(members.map((item) => item.getAttribute('aria-label'))).toEqual(['Member 1', 'Member 2', 'Member 3'])
    expect(within(members[0]).getByTestId('seed-value-prompt-1').textContent).toBe('prompt body\nkept')
    expect(within(members[2]).getByText('File is missing.')).toBeInTheDocument()
    expect(within(screen.getByRole('table', { name: 'Examples' })).queryByRole('img')).not.toBeInTheDocument()
    expect(document.querySelector('audio, video')).toBeNull()
    await waitFor(() => expect(screen.getByRole('heading', { level: 2, name: 'alpha' })).toHaveFocus())
    const detailCall = mockGet.mock.calls.find((call) => String(call[0]).startsWith('/datasets/seeds/'))
    expect(detailCall?.[0]).toBe('/datasets/seeds/example-1')
    expect(paramsOf(detailCall).selection_key).toBe(DATASET.selection_key)
    expect(currentUrl().searchParams.get('example')).toBe('example-1')

    await user.click(screen.getByRole('button', { name: 'Refresh page' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()
    expect(currentUrl().searchParams.get('example')).toBe('example-1')
  })

  it('tabs to the first visible example when the open example is on another page', async () => {
    const user = userEvent.setup()
    renderBrowser()
    await screen.findByText('alpha')

    await user.click(screen.getByRole('button', { name: 'Show alpha' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('later preview')).toBeInTheDocument()
    expect(currentUrl().searchParams.get('example')).toBe('example-1')
    expect(screen.queryByRole('button', { name: 'Hide alpha' })).not.toBeInTheDocument()

    screen.getByRole('combobox', { name: 'Examples per page' }).focus()
    await user.tab()

    const showBeta = screen.getByRole('button', { name: 'Show beta' })
    expect(showBeta).toHaveFocus()
    expect(showBeta).toHaveAttribute('tabindex', '0')
    await user.keyboard('{ArrowDown}')
    expect(showBeta).toHaveFocus()
  })

  it('moves a single selection with the keyboard', async () => {
    const user = userEvent.setup()
    renderBrowser()
    const firstButton = await screen.findByRole('button', { name: 'Show alpha' })
    firstButton.focus()
    await user.keyboard('{ArrowDown}')

    expect(await screen.findByRole('button', { name: 'Hide beta' })).toHaveFocus()
    expect(screen.getByRole('button', { name: 'Show alpha' })).toHaveAttribute('aria-pressed', 'false')
    expect(currentUrl().searchParams.get('example')).toBe('example-2')
  })

  it('restarts from the first page only after a rejected cursor is dismissed', async () => {
    const user = userEvent.setup()
    renderBrowser('&cursor=bad&search=policy&modality=text', {
      catalogPath: '/datasets',
      cursorGeneration: 'gen-1',
      seedCursors: [null],
    })

    expect(await screen.findByText('This page link is not valid for these filters.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start from the first page' })).toBeInTheDocument()
    expect(currentUrl().searchParams.get('cursor')).toBe('bad')
    expect(currentUrl().searchParams.get('search')).toBe('policy')
    expect(currentUrl().searchParams.get('modality')).toBe('text')
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()
    expect(seedListCalls().every((call) => paramsOf(call).cursor === 'bad')).toBe(true)

    await user.click(screen.getByRole('button', { name: 'Start from the first page' }))

    expect(await screen.findByText('alpha')).toBeInTheDocument()
    await waitFor(() => expect(currentUrl().searchParams.get('cursor')).toBeNull())
    expect(currentUrl().searchParams.get('search')).toBe('policy')
    expect(currentUrl().searchParams.get('modality')).toBe('text')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    const last = seedListCalls()[seedListCalls().length - 1]
    expect(paramsOf(last).cursor).toBeUndefined()
    expect(paramsOf(last).search).toBe('policy')
    expect(paramsOf(last).modality).toEqual(['text'])
    expect(seedListCalls().filter((call) => paramsOf(call).cursor == null).length).toBeGreaterThan(0)
    expect(seedListCalls().every((call) => paramsOf(call).cursor == null || paramsOf(call).cursor === 'bad')).toBe(true)
  })

  it('focuses retry for a bad cursor and does not replace that cursor', async () => {
    const user = userEvent.setup()
    renderBrowser('&cursor=bad')

    expect(await screen.findByText('This page link is not valid for these filters.')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry' })).toHaveFocus())
    expect(screen.queryByText('alpha')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(seedListCalls().length).toBeGreaterThanOrEqual(2))
    expect(currentUrl().searchParams.get('cursor')).toBe('bad')
    expect(seedListCalls().every((call) => paramsOf(call).cursor === 'bad')).toBe(true)
  })

  it('does not fetch when the cursor is repeated', async () => {
    renderBrowser('&cursor=one&cursor=two')

    expect(await screen.findByText('This page link has more than one cursor.')).toBeInTheDocument()
    expect(seedListCalls()).toEqual([])
    expect(screen.getByRole('button', { name: 'Start from the first page' })).toHaveFocus()
  })

  it('renders duplicate example ids and an empty dataset without calling them a failed request', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/datasets/seeds') {
        return Promise.resolve({ data: listPage([FIRST, DUPLICATE], { total: 2 }) })
      }
      return Promise.reject(new Error(String(url)))
    })
    renderBrowser()
    const table = await screen.findByRole('table', { name: 'Examples' })
    expect(within(table).getByText('alpha')).toBeInTheDocument()
    expect(within(table).getByText('alpha copy')).toBeInTheDocument()
  })

  it('refreshes catalog metadata once when an unfiltered dataset has no stored rows', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/datasets/seeds') return Promise.resolve({ data: listPage([], { total: 0 }) })
      return Promise.reject(new Error(String(url)))
    })
    renderBrowser()
    expect(await screen.findByText('No examples in this dataset')).toBeInTheDocument()
    await waitFor(() => expect(onCatalogStale).toHaveBeenCalledTimes(1))
  })

  it.each([
    { initialGeneration: '', initialReady: false },
    { initialGeneration: '', initialReady: true },
    { initialGeneration: 'gen-1', initialReady: false },
  ])('keeps saved paging and selection while runtime is $initialGeneration / $initialReady', async ({ initialGeneration, initialReady }) => {
    const user = userEvent.setup()
    generation = initialGeneration
    ready = initialReady
    renderBrowser('&cursor=cursor-2&example=example-1', {
      cursorGeneration: 'gen-1',
      seedCursors: [null],
      catalogPath: '/datasets?q=policy',
    })
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()
    expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
    expect(currentUrl().searchParams.get('example')).toBe('example-1')
    expect(exampleStatus()).toHaveTextContent('12 matching examples, page 2')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeEnabled()

    generation = 'gen-1'
    ready = true
    await user.click(screen.getByRole('button', { name: 'Apply runtime' }))

    await waitFor(() => {
      expect(screen.getByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()
      expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
      expect(currentUrl().searchParams.get('example')).toBe('example-1')
      expect(exampleStatus()).toHaveTextContent('12 matching examples, page 2')
    })
    expect(seedListCalls().every((call) => paramsOf(call).cursor === 'cursor-2')).toBe(true)
    expect(screen.getByLabelText('Catalog return')).toHaveTextContent('/datasets?q=policy')

    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    await waitFor(() => expect(currentUrl().searchParams.get('cursor')).toBeNull())
    expect(currentUrl().searchParams.get('example')).toBe('example-1')
  })

  it('waits for readiness before invalidating saved paging from a different runtime', async () => {
    const user = userEvent.setup()
    generation = ''
    ready = false
    renderBrowser('&cursor=cursor-2&example=example-1&search=policy&limit=50', {
      cursorGeneration: 'gen-1',
      seedCursors: [null],
      catalogPath: '/datasets?q=policy',
    })
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()

    generation = 'gen-2'
    await user.click(screen.getByRole('button', { name: 'Apply runtime' }))
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()
    expect(currentUrl().searchParams.get('cursor')).toBe('cursor-2')
    expect(currentUrl().searchParams.get('example')).toBe('example-1')

    ready = true
    await user.click(screen.getByRole('button', { name: 'Apply runtime' }))

    await waitFor(() => expect(exampleStatus()).toHaveTextContent('12 matching examples, page 1'))
    expect(currentUrl().searchParams.get('cursor')).toBeNull()
    expect(currentUrl().searchParams.get('example')).toBeNull()
    expect(currentUrl().searchParams.get('search')).toBe('policy')
    expect(currentUrl().searchParams.get('limit')).toBe('50')
    expect(screen.queryByRole('heading', { level: 2, name: 'alpha' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByLabelText('Catalog return')).toHaveTextContent('/datasets?q=policy')
  })

  it('shows a way back when the example id does not match a stored row', async () => {
    const user = userEvent.setup()
    renderBrowser('&example=AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE')

    expect(await screen.findByText('This example is not in the dataset.')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { level: 2, name: 'alpha' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Example detail' })).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Back to the examples' }))
    await waitFor(() => expect(currentUrl().searchParams.get('example')).toBeNull())
    expect(screen.getByRole('table', { name: 'Examples' })).toBeInTheDocument()
    expect(screen.queryByText('This example is not in the dataset.')).not.toBeInTheDocument()
  })

  it('shows a way back when the example request is not found', async () => {
    mockGet.mockImplementation((url: string) => {
      if (url === '/datasets/seeds') return Promise.resolve({ data: listPage([FIRST, SECOND]) })
      if (String(url).startsWith('/datasets/seeds/')) {
        return Promise.reject({
          isAxiosError: true,
          response: { status: 404, data: { detail: 'missing' } },
        })
      }
      return Promise.reject(new Error(String(url)))
    })
    renderBrowser('&example=example-1')

    expect(await screen.findByText('This example is not in the dataset.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to the examples' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Example detail' })).not.toBeInTheDocument()
  })

  it('drops the open example when the runtime generation changes', async () => {
    const { rerender } = renderBrowser('&example=example-1')
    expect(await screen.findByRole('heading', { level: 2, name: 'alpha' })).toBeInTheDocument()

    generation = 'gen-2'
    rerender(
      <FluentProvider theme={webLightTheme}>
        <MemoryRouter initialEntries={[{ pathname: '/datasets/detail', search: `?selection_key=${encodeURIComponent(DATASET.selection_key)}&example=example-1`, state: { cursorGeneration: 'gen-1', catalogPath: '/datasets', seedCursors: [] } }]}>
          <Harness dataset={DATASET} />
        </MemoryRouter>
      </FluentProvider>,
    )

    await waitFor(() => expect(screen.queryByRole('heading', { level: 2, name: 'alpha' })).not.toBeInTheDocument())
  })
})
