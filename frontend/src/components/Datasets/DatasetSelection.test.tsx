import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import { apiClient } from '@/services/api'
import type { DatasetInfo } from '@/types'

import { datasetDetailPath } from '@/utils/routeParams'

import DatasetSelection from './DatasetSelection'

let mockGet: jest.SpyInstance

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

function renderSelection(initialPath: string) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route path="/datasets" element={<p>Catalog</p>} />
          <Route path="/datasets/detail" element={<DatasetSelection />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

const LOADED = makeDataset({
  name: 'harmbench',
  selection_key: 'dataset:named:harmbench',
})

describe('DatasetSelection', () => {
  beforeEach(() => {
    mockGet = jest.spyOn(apiClient, 'get').mockResolvedValue({ data: { items: [LOADED] } })
  })

  afterEach(() => {
    expectOnlyLoadedDatasetGets()
    jest.restoreAllMocks()
  })

  it('focuses the dataset name when the selection key matches the catalog', async () => {
    renderSelection(datasetDetailPath(LOADED.selection_key))

    const heading = await screen.findByRole('heading', { level: 1, name: 'harmbench' })
    await waitFor(() => expect(heading).toHaveFocus())
    expect(screen.getByText('Individual examples are not listed in this view.')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'harmbench' })).not.toBeInTheDocument()
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
  })

  it('truncates a long unknown selection key and renders it as text', async () => {
    const key = `<img src=x onerror=alert(1)>${'a'.repeat(200)}`
    renderSelection(datasetDetailPath(key))

    const shown = `${key.slice(0, 79)}…`
    expect(await screen.findByText(shown)).toBeInTheDocument()
    expect(screen.queryByText(key)).not.toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('script')).toBeNull()
  })

  it('does not choose between repeated keys or fetch them', async () => {
    renderSelection('/datasets/detail?selection_key=dataset:named:harmbench&selection_key=dataset:named:other')

    expect(await screen.findByRole('heading', { name: 'Dataset not found' })).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'harmbench' })).not.toBeInTheDocument()
    expect(screen.queryByText('dataset:named:harmbench')).not.toBeInTheDocument()
  })

  it('shows not-found when the selection key is missing', async () => {
    renderSelection('/datasets/detail')

    expect(await screen.findByRole('heading', { name: 'Dataset not found' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to datasets' })).toHaveAttribute('href', '/datasets')
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

    await user.click(screen.getByRole('link', { name: 'Back to datasets' }))
    expect(await screen.findByText('Catalog')).toBeInTheDocument()
  })
})
