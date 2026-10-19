import { useState } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { analyticsApi } from '@/services/api'
import { ANALYTICS_OPERATION_FILTER, makeAnalyticsFacets } from '@/test-utils/analyticsFixtures'
import type { AttackAnalyticsFacets, AttackAnalyticsFilters } from '@/types'

import AnalyticsFilters from './AnalyticsFilters'

jest.mock('@/services/api', () => ({
  analyticsApi: { facets: jest.fn() },
}))
const mockFacets = jest.mocked(analyticsApi.facets)

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

interface FiltersHarnessProps {
  readonly initialFilters?: AttackAnalyticsFilters
}

function FiltersHarness({ initialFilters = { dimensions: [], outcomes: [] } }: FiltersHarnessProps) {
  const [filters, setFilters] = useState<AttackAnalyticsFilters>(initialFilters)
  const [refreshVersion, setRefreshVersion] = useState(0)
  return <>
    <AnalyticsFilters filters={filters} refreshVersion={refreshVersion} onChange={(next: AttackAnalyticsFilters) => { setFilters(next); return true }} />
    <output aria-label="Applied filters">{JSON.stringify(filters)}</output>
    <button onClick={() => { setRefreshVersion((version: number) => version + 1) }}>Reload report</button>
  </>
}

describe('AnalyticsFilters', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockFacets.mockReset().mockResolvedValue(makeAnalyticsFacets())
  })
  afterEach(() => { jest.useRealTimers() })

  it('should request only an opened facet, refresh it on Reload, and invalidate closed options', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    expect(mockFacets).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await screen.findByRole('checkbox', { name: 'Nightly' })
    expect(mockFacets).toHaveBeenCalledTimes(1)
    expect(mockFacets).toHaveBeenLastCalledWith(expect.objectContaining({ dimension: { name: 'operation' }, search: '', offset: 0, limit: 50 }), expect.any(AbortSignal))
    await user.click(screen.getByRole('button', { name: 'Reload report' }))
    await waitFor(() => { expect(mockFacets).toHaveBeenCalledTimes(2) })
    await user.click(screen.getByRole('button', { name: 'Close filter editor' }))
    await user.click(screen.getByRole('button', { name: 'Reload report' }))
    expect(mockFacets).toHaveBeenCalledTimes(2)
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await waitFor(() => { expect(mockFacets).toHaveBeenCalledTimes(3) })
  })

  it('should debounce searches and retain typed selected values when no alternatives match', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await act(async () => { await jest.advanceTimersByTimeAsync(250) })
    await user.click(screen.getByRole('checkbox', { name: 'Unknown' }))
    await user.click(screen.getByRole('checkbox', { name: 'Unknown (missing metadata)' }))
    mockFacets.mockResolvedValue(makeAnalyticsFacets({ items: [] }))
    await user.type(screen.getByLabelText('Search values'), 'does-not-match')
    expect(mockFacets).toHaveBeenCalledTimes(1)
    await act(async () => { await jest.advanceTimersByTimeAsync(250) })
    expect(mockFacets).toHaveBeenCalledTimes(2)
    expect(mockFacets).toHaveBeenLastCalledWith(expect.objectContaining({ search: 'does-not-match', offset: 0 }), expect.any(AbortSignal))
    const selected = screen.getByRole('group', { name: 'Selected filter values' })
    expect(within(selected).getByRole('checkbox', { name: 'Unknown' })).toBeChecked()
    expect(within(selected).getByRole('checkbox', { name: 'Unknown (missing metadata)' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent('"kind":"missing","value":null')
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent('"kind":"value","value":"Unknown"')
  })

  it('should show a fresh read when returning to an earlier facet search', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    let resolveOther: ((value: AttackAnalyticsFacets) => void) | undefined
    mockFacets.mockResolvedValueOnce(makeAnalyticsFacets())
      .mockReturnValueOnce(new Promise<AttackAnalyticsFacets>((resolve: (value: AttackAnalyticsFacets) => void) => { resolveOther = resolve }))
      .mockResolvedValueOnce(makeAnalyticsFacets({ items: [] }))
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await act(async () => { await jest.advanceTimersByTimeAsync(250) })
    await user.type(screen.getByLabelText('Search values'), 'other')
    await act(async () => { await jest.advanceTimersByTimeAsync(250) })
    await user.clear(screen.getByLabelText('Search values'))
    expect(screen.getByRole('progressbar', { name: 'Loading filter values' })).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Nightly' })).not.toBeInTheDocument()
    await act(async () => {
      await jest.advanceTimersByTimeAsync(250)
      resolveOther?.(makeAnalyticsFacets())
    })
    expect(screen.getByText(/No values match this search/)).toBeInTheDocument()
    expect(mockFacets).toHaveBeenCalledTimes(3)
  })

  it('should require a valid explicit custom label key before looking up values', async () => {
    jest.useFakeTimers()
    const user = userEvent.setup({ advanceTimers: jest.advanceTimersByTime })
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Add filter' }))
    await user.selectOptions(screen.getByLabelText('Filter dimension'), 'label')
    await user.type(screen.getByLabelText('Filter dimension label key'), 'team.region')
    await act(async () => { await jest.advanceTimersByTimeAsync(500) })
    expect(mockFacets).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Use label' }))
    await act(async () => { await jest.advanceTimersByTimeAsync(250) })
    expect(mockFacets).toHaveBeenCalledTimes(1)
    expect(mockFacets).toHaveBeenCalledWith(expect.objectContaining({ dimension: { name: 'label', label_key: 'team.region' } }), expect.any(AbortSignal))
  })

  it('should support response converters, ALL matching, known-empty pipelines and unknown metadata', async () => {
    const user = userEvent.setup()
    mockFacets.mockResolvedValue(makeAnalyticsFacets({ items: [
      { key: { kind: 'value', value: 'ConverterA' }, label: 'ConverterA' },
      { key: { kind: 'no_converters', value: null }, label: 'No converters' },
      { key: { kind: 'missing', value: null }, label: 'Unknown' },
    ] }))
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Add filter' }))
    await user.selectOptions(screen.getByLabelText('Filter dimension'), 'response_converters')
    await user.selectOptions(screen.getByLabelText('Converter matching'), 'all')
    expect(screen.getByText(/All selected converters must match/)).toBeInTheDocument()
    await user.click(await screen.findByRole('checkbox', { name: 'ConverterA' }))
    await user.click(screen.getByRole('checkbox', { name: 'Unknown (missing metadata)' }))
    expect(screen.getByRole('checkbox', { name: 'No converters (empty pipeline)' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent('"converter_direction":"response"')
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent('"match_mode":"all"')
    const chip = screen.getByRole('button', { name: /^Response converter \(ALL\)/ })
    await user.click(chip)
    expect(screen.getByLabelText('Converter matching')).toHaveValue('all')
    expect(within(screen.getByRole('group', { name: 'Selected filter values' })).getByRole('checkbox', { name: 'ConverterA' })).toBeChecked()
  })

  it('should send all predicates for server-side facet exclusion and edit only the selected repeated dimension', async () => {
    const user = userEvent.setup()
    const filters: AttackAnalyticsFilters = {
      dimensions: [
        ANALYTICS_OPERATION_FILTER,
        { ...ANALYTICS_OPERATION_FILTER, values: [{ kind: 'value', value: 'Other operation' }] },
      ],
      outcomes: ['failure'],
      updated_after: '2026-01-01T00:00:00Z',
    }
    render(<TestWrapper><FiltersHarness initialFilters={filters} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Operation (ANY): Nightly' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Unknown' }))
    expect(mockFacets).toHaveBeenCalledWith(
      expect.objectContaining({ dimension: { name: 'operation' }, filters }),
      expect.any(AbortSignal),
    )
    await user.click(screen.getByRole('button', { name: 'Apply filter' }))
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent(JSON.stringify({
      ...filters,
      dimensions: [
        { ...ANALYTICS_OPERATION_FILTER, values: [...ANALYTICS_OPERATION_FILTER.values, { kind: 'value', value: 'Unknown' }] },
        filters.dimensions[1],
      ],
    }))
  })

  it('should preserve untouched timestamp precision and time zone, and clear only an edited bound', async () => {
    const user = userEvent.setup()
    const filters: AttackAnalyticsFilters = {
      dimensions: [], outcomes: [],
      updated_after: '2026-01-01T08:00:05.123456-07:00',
      updated_before: '2026-02-01T08:00:05.123456-07:00',
    }
    render(<TestWrapper><FiltersHarness initialFilters={filters} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Last updated' }))
    await user.click(screen.getByRole('button', { name: 'Apply range' }))
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent(JSON.stringify(filters))
    await user.click(screen.getByRole('button', { name: 'Last updated' }))
    await user.clear(screen.getByLabelText('Last updated after'))
    await user.click(screen.getByRole('button', { name: 'Apply range' }))
    expect(screen.getByLabelText('Applied filters')).toHaveTextContent(JSON.stringify({ ...filters, updated_after: null }))
    expect(mockFacets).not.toHaveBeenCalled()
  })

  it('should page one facet with server offsets and surface errors without discarding selections', async () => {
    const user = userEvent.setup()
    mockFacets.mockResolvedValueOnce(makeAnalyticsFacets({ has_more: true, next_offset: 17 }))
      .mockRejectedValueOnce(new Error('Facet read timed out'))
      .mockResolvedValueOnce(makeAnalyticsFacets({ items: [] }))
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Nightly' }))
    await user.click(screen.getByRole('button', { name: 'Next values' }))
    expect(await screen.findByText(/Could not load filter values.*Facet read timed out/)).toBeInTheDocument()
    expect(mockFacets).toHaveBeenLastCalledWith(expect.objectContaining({ offset: 17 }), expect.any(AbortSignal))
    expect(screen.getByRole('checkbox', { name: 'Nightly' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Retry values' }))
    await screen.findByText(/No values match this search/)
    expect(mockFacets).toHaveBeenCalledTimes(3)
  })

  it('should cancel a closed facet and ignore its late response in another dimension', async () => {
    const user = userEvent.setup()
    let resolveFirst: ((value: AttackAnalyticsFacets) => void) | undefined
    mockFacets.mockReturnValueOnce(new Promise<AttackAnalyticsFacets>((resolve: (value: AttackAnalyticsFacets) => void) => { resolveFirst = resolve }))
      .mockResolvedValueOnce(makeAnalyticsFacets({ items: [{ key: { kind: 'value', value: 'Alice' }, label: 'Alice' }] }))
    render(<TestWrapper><FiltersHarness /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Operation' }))
    await waitFor(() => { expect(mockFacets).toHaveBeenCalledTimes(1) })
    const signal = mockFacets.mock.calls[0][1]
    await user.click(screen.getByRole('button', { name: 'Operator' }))
    await screen.findByRole('checkbox', { name: 'Alice' })
    expect(signal?.aborted).toBe(true)
    await act(async () => { resolveFirst?.(makeAnalyticsFacets()) })
    expect(screen.queryByRole('checkbox', { name: 'Nightly' })).not.toBeInTheDocument()
  })
})
