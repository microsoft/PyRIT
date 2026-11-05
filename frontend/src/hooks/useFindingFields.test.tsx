import { StrictMode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { operationsApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import { CONTOSO_FINDING_FIELDS, DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { FindingFields } from '@/types'

import { useFindingFields } from './useFindingFields'
import { useRuntime } from './useRuntime'

jest.mock('@/services/api', () => ({ operationsApi: { getFindingFields: jest.fn() } }))
jest.mock('./useRuntime', () => ({ useRuntime: jest.fn() }))
const mockGetFindingFields = jest.mocked(operationsApi.getFindingFields)
const mockRuntime = jest.mocked(useRuntime)

describe('useFindingFields', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockGetFindingFields.mockReset()
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'one' })
  })

  it('loads the finding fields once', async () => {
    mockGetFindingFields.mockResolvedValue(CONTOSO_FINDING_FIELDS)

    const { result, rerender } = renderHook(() => useFindingFields())
    expect(result.current.fields).toBeNull()

    await waitFor(() => expect(result.current.fields).toBe(CONTOSO_FINDING_FIELDS))
    rerender()
    expect(result.current.error).toBe('')
    expect(mockGetFindingFields).toHaveBeenCalledTimes(1)
  })

  it('loads the fields again when the runtime generation changes, keeping the last ones meanwhile', async () => {
    const reloaded = deferred<FindingFields>()
    mockGetFindingFields.mockResolvedValueOnce(DEFAULT_FINDING_FIELDS_FIXTURE).mockReturnValueOnce(reloaded.promise)

    const { result, rerender } = renderHook(() => useFindingFields())
    await waitFor(() => expect(result.current.fields).toBe(DEFAULT_FINDING_FIELDS_FIXTURE))

    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    rerender()
    expect(mockGetFindingFields).toHaveBeenCalledTimes(2)
    expect(result.current.fields).toBe(DEFAULT_FINDING_FIELDS_FIXTURE)

    await act(async () => { reloaded.resolve(CONTOSO_FINDING_FIELDS) })
    expect(result.current.fields).toBe(CONTOSO_FINDING_FIELDS)
    expect(result.current.error).toBe('')
  })

  it('changes orderRevision only when a reload brings a different severity order', async () => {
    mockGetFindingFields
      .mockResolvedValueOnce(DEFAULT_FINDING_FIELDS_FIXTURE)
      .mockResolvedValueOnce({ ...DEFAULT_FINDING_FIELDS_FIXTURE })
      .mockResolvedValueOnce(CONTOSO_FINDING_FIELDS)

    const { result, rerender } = renderHook(() => useFindingFields())
    await waitFor(() => expect(result.current.fields).toBe(DEFAULT_FINDING_FIELDS_FIXTURE))
    const loaded = result.current.orderRevision

    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    rerender()
    await waitFor(() => expect(result.current.fields).not.toBe(DEFAULT_FINDING_FIELDS_FIXTURE))
    expect(result.current.orderRevision).toBe(loaded)

    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'three' })
    rerender()
    await waitFor(() => expect(result.current.fields).toBe(CONTOSO_FINDING_FIELDS))
    expect(result.current.orderRevision).not.toBe(loaded)
  })

  it('reports a load failure and retries', async () => {
    mockGetFindingFields
      .mockRejectedValueOnce(new Error('The Contoso backend is unavailable.'))
      .mockResolvedValueOnce(DEFAULT_FINDING_FIELDS_FIXTURE)

    const { result } = renderHook(() => useFindingFields())
    await waitFor(() => expect(result.current.error).toBe('The Contoso backend is unavailable.'))
    expect(result.current.fields).toBeNull()

    act(() => { result.current.retry() })
    expect(result.current.error).toBe('')

    await waitFor(() => expect(result.current.fields).toBe(DEFAULT_FINDING_FIELDS_FIXTURE))
    expect(result.current.error).toBe('')
    expect(mockGetFindingFields).toHaveBeenCalledTimes(2)
  })

  it('ignores a response after unmount', async () => {
    const unmounted = deferred<FindingFields>()
    const mounted = deferred<FindingFields>()
    mockGetFindingFields.mockReturnValueOnce(unmounted.promise).mockReturnValueOnce(mounted.promise)

    // StrictMode unmounts and remounts once, so the first request belongs to an unmounted effect.
    const { result } = renderHook(() => useFindingFields(), { wrapper: StrictMode })
    await act(async () => { mounted.resolve(CONTOSO_FINDING_FIELDS) })
    await act(async () => { unmounted.resolve(DEFAULT_FINDING_FIELDS_FIXTURE) })

    expect(mockGetFindingFields).toHaveBeenCalledTimes(2)
    expect(result.current.fields).toBe(CONTOSO_FINDING_FIELDS)
  })

  it('loads only while enabled, again each time it is enabled, and clears an earlier failure', async () => {
    mockGetFindingFields
      .mockRejectedValueOnce(new Error('The Contoso backend is unavailable.'))
      .mockResolvedValueOnce(CONTOSO_FINDING_FIELDS)

    const { result, rerender } = renderHook(({ enabled }: { enabled: boolean }) => useFindingFields(enabled), {
      initialProps: { enabled: false },
    })
    expect(mockGetFindingFields).not.toHaveBeenCalled()

    rerender({ enabled: true })
    await waitFor(() => expect(result.current.error).toBe('The Contoso backend is unavailable.'))
    rerender({ enabled: false })
    mockRuntime.mockReturnValue({ ready: true, state: 'ready', generation: 'two' })
    rerender({ enabled: false })
    expect(mockGetFindingFields).toHaveBeenCalledTimes(1)
    rerender({ enabled: true })
    await waitFor(() => expect(result.current.fields).toBe(CONTOSO_FINDING_FIELDS))
    expect(result.current.error).toBe('')
    expect(mockGetFindingFields).toHaveBeenCalledTimes(2)
  })
})
