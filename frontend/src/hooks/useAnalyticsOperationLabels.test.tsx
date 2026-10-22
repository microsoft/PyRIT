import { act, renderHook, waitFor } from '@testing-library/react'

import { operationsApi } from '@/services/api'
import { ANALYTICS_NIGHTLY_ID, ANALYTICS_OPERATION_FILTER, ANALYTICS_OPERATIONS, ANALYTICS_TEST_TIME } from '@/test-utils/analyticsFixtures'
import { deferred } from '@/test-utils/deferred'
import type { AttackAnalyticsFilters, OperationListItem, OperationListResponse } from '@/types'

import { useAnalyticsOperationLabels } from './useAnalyticsOperationLabels'
import { useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({ operationsApi: { list: jest.fn() } }))
jest.mock('./useRuntime', () => ({ useRuntime: jest.fn() }))
const mockList = jest.mocked(operationsApi.list)
const mockRuntime = jest.mocked(useRuntime)

const DELETED_ID = '5f0c8a8e-3a52-4c3e-9f0a-2f8b1c7d6e54'
const WEEKLY: OperationListItem = {
  id: '0b6f2d8e-8a0e-4b8c-9a51-3f3d2b7c9e10', name: 'Weekly', created_at: ANALYTICS_TEST_TIME, finding_counts: {},
}
const RESTORED_FILTERS: AttackAnalyticsFilters = {
  dimensions: [
    { ...ANALYTICS_OPERATION_FILTER, values: [...ANALYTICS_OPERATION_FILTER.values, { kind: 'value', value: DELETED_ID }] },
    { dimension: { name: 'operator' }, values: [{ kind: 'value', value: ANALYTICS_NIGHTLY_ID }], match_mode: 'any' },
  ],
  outcomes: [],
}
const NIGHTLY_FILTERS: AttackAnalyticsFilters = { dimensions: [ANALYTICS_OPERATION_FILTER], outcomes: [] }
const NIGHTLY_AND_WEEKLY_FILTERS: AttackAnalyticsFilters = {
  dimensions: [ANALYTICS_OPERATION_FILTER, { ...ANALYTICS_OPERATION_FILTER, values: [{ kind: 'value', value: WEEKLY.id }] }],
  outcomes: [],
}
const RENAMED_OPERATIONS: OperationListResponse = { items: [{ ...ANALYTICS_OPERATIONS.items[0], name: 'Renamed' }] }

interface LabelsProps {
  readonly current: AttackAnalyticsFilters
  readonly refreshVersion?: number
}

function renderLabels(filters: AttackAnalyticsFilters = RESTORED_FILTERS) {
  return renderHook(
    ({ current, refreshVersion = 0 }: LabelsProps) => useAnalyticsOperationLabels(current, refreshVersion),
    { initialProps: { current: filters } },
  )
}

/** A macrotask runs only after every pending promise callback, so the lookup has settled. */
function flushPromises(): Promise<void> {
  return new Promise((resolve: () => void) => { setTimeout(resolve, 0) })
}

async function settle(): Promise<void> {
  await act(async () => { await flushPromises() })
}

describe('useAnalyticsOperationLabels', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
    mockList.mockReset().mockResolvedValue(ANALYTICS_OPERATIONS)
  })
  afterEach(() => { jest.restoreAllMocks() })

  it('should map restored Operation IDs to names with one list call', async () => {
    const { result, rerender } = renderLabels()
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    rerender({ current: { ...RESTORED_FILTERS, outcomes: ['success'] } })
    await settle()
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' })
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('should keep the map unchanged and not call again after a failed lookup', async () => {
    const { result, rerender } = renderLabels(NIGHTLY_FILTERS)
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    mockList.mockRejectedValueOnce(new Error('Operations unavailable'))
    rerender({ current: NIGHTLY_AND_WEEKLY_FILTERS })
    await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(2) })
    await settle()
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' })
    rerender({ current: { ...NIGHTLY_AND_WEEKLY_FILTERS, outcomes: ['failure'] } })
    await settle()
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' })
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('should merge a later lookup for a new Operation ID without dropping earlier names', async () => {
    mockList.mockResolvedValueOnce(ANALYTICS_OPERATIONS).mockResolvedValueOnce({ items: [WEEKLY] })
    const { result, rerender } = renderLabels(NIGHTLY_FILTERS)
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    rerender({ current: NIGHTLY_AND_WEEKLY_FILTERS })
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly', [WEEKLY.id]: 'Weekly' }) })
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('should not update after unmounting before the lookup resolves', async () => {
    const consoleError = jest.spyOn(console, 'error')
    let resolveList: ((value: OperationListResponse) => void) | undefined
    mockList.mockReturnValueOnce(new Promise<OperationListResponse>((resolve: (value: OperationListResponse) => void) => {
      resolveList = resolve
    }))
    const { result, unmount } = renderLabels()
    await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(1) })
    unmount()
    // Resolve outside act so any update to the unmounted hook would surface as a React warning.
    resolveList?.(ANALYTICS_OPERATIONS)
    await flushPromises()
    expect(result.current).toEqual({})
    expect(consoleError).not.toHaveBeenCalled()
  })

  it('should not call the list without Operation ID filters', async () => {
    const { result } = renderLabels({
      dimensions: [
        { dimension: { name: 'operation' }, values: [{ kind: 'legacy_operation_name', value: 'Nightly' }, { kind: 'missing', value: null }], match_mode: 'any' },
        { dimension: { name: 'operator' }, values: [{ kind: 'value', value: ANALYTICS_NIGHTLY_ID }], match_mode: 'any' },
      ],
      outcomes: [],
    })
    await settle()
    expect(result.current).toEqual({})
    expect(mockList).not.toHaveBeenCalled()
  })

  it('should wait for the runtime before reading', async () => {
    mockRuntime.mockReturnValue({ ready: false, state: 'connecting', generation: '' })
    const { result, rerender } = renderLabels()
    await settle()
    expect(mockList).not.toHaveBeenCalled()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
    rerender({ current: RESTORED_FILTERS })
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    expect(mockList).toHaveBeenCalledTimes(1)
  })

  it('should look up names again on Reload and keep the old name until the new one arrives', async () => {
    const renamed = deferred<OperationListResponse>()
    mockList.mockResolvedValueOnce(ANALYTICS_OPERATIONS).mockReturnValueOnce(renamed.promise)
    const { result, rerender } = renderLabels(NIGHTLY_FILTERS)
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    rerender({ current: NIGHTLY_FILTERS, refreshVersion: 1 })
    await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(2) })
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' })
    renamed.resolve(RENAMED_OPERATIONS)
    await settle()
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Renamed' })
  })

  it('should look up names again when the runtime generation changes', async () => {
    mockList.mockResolvedValueOnce(ANALYTICS_OPERATIONS).mockResolvedValueOnce(RENAMED_OPERATIONS)
    const { result, rerender } = renderLabels(NIGHTLY_FILTERS)
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly' }) })
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    rerender({ current: NIGHTLY_FILTERS })
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Renamed' }) })
    expect(mockList).toHaveBeenCalledTimes(2)
  })

  it('should drop the name of an Operation missing from the refreshed list', async () => {
    mockList.mockResolvedValueOnce({ items: [...ANALYTICS_OPERATIONS.items, WEEKLY] }).mockResolvedValueOnce({ items: [WEEKLY] })
    const { result, rerender } = renderLabels(NIGHTLY_AND_WEEKLY_FILTERS)
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Nightly', [WEEKLY.id]: 'Weekly' }) })
    rerender({ current: NIGHTLY_AND_WEEKLY_FILTERS, refreshVersion: 1 })
    await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(2) })
    await settle()
    expect(result.current).toEqual({ [WEEKLY.id]: 'Weekly' })
  })

  it('should ignore a lookup from an earlier Reload that resolves after a newer one', async () => {
    const stale = deferred<OperationListResponse>()
    mockList.mockReturnValueOnce(stale.promise).mockResolvedValueOnce(RENAMED_OPERATIONS)
    const { result, rerender } = renderLabels(NIGHTLY_FILTERS)
    await waitFor(() => { expect(mockList).toHaveBeenCalledTimes(1) })
    rerender({ current: NIGHTLY_FILTERS, refreshVersion: 1 })
    await waitFor(() => { expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Renamed' }) })
    stale.resolve(ANALYTICS_OPERATIONS)
    await settle()
    expect(result.current).toEqual({ [ANALYTICS_NIGHTLY_ID]: 'Renamed' })
    expect(mockList).toHaveBeenCalledTimes(2)
  })
})
