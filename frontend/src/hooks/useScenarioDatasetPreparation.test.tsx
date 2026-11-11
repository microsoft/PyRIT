import { StrictMode, type ReactNode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import { deferred } from '@/test-utils/deferred'
import type { DatasetInfo, ScenarioDatasetLoadResponse } from '@/types'

import { useScenarioDatasetPreparation } from './useScenarioDatasetPreparation'

jest.mock('@/services/api', () => ({ scenariosApi: { loadDatasets: jest.fn() } }))
let generation = 'one'
jest.mock('./useRuntime', () => ({ useRuntime: () => ({ ready: true, generation }) }))
const load = jest.mocked(scenariosApi.loadDatasets)
const onPrepared = jest.fn()
type Options = Parameters<typeof useScenarioDatasetPreparation>[0]

function stored(name: string): DatasetInfo {
  return {
    name, selection_key: `dataset:named:${name}`, loaded: true, provider_available: true,
    logical_examples: 1, modalities: ['text'], harm_categories: [], has_unlabeled_harm_categories: false,
  }
}

function loaded(...names: string[]): ScenarioDatasetLoadResponse {
  return {
    items: names.map(stored),
    settings: { sources: [], max_per_dataset: 5, max_total: 'all', fetch: 'if_missing', filters: {} },
  }
}

function options(names: string[] = ['missing'], edits: Partial<Options> = {}): Options {
  return {
    scenarioName: 'generic',
    datasetNames: names,
    request: { datasets: { sources: names.map((name) => ({ name })) } },
    metadata: [], metadataLoading: false, metadataError: null, enabled: true, onPrepared,
    ...edits,
  }
}

describe('useScenarioDatasetPreparation', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    generation = 'one'
    load.mockReset().mockResolvedValue(loaded('missing'))
  })

  it('reuses stored selected data and does nothing for an empty selection', () => {
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), {
      initialProps: options(['stored'], { metadata: [stored('stored')] }),
    })
    expect(view.result.current.prepared).toBe(true)
    view.rerender(options([], { request: null }))
    expect(view.result.current.prepared).toBe(true)
    expect(load).not.toHaveBeenCalled()
  })

  it('prepares initial mixed selections once in StrictMode using the actual selected request', async () => {
    load.mockResolvedValue(loaded('stored', 'missing'))
    const input = options(['stored', 'missing'], { metadata: [stored('stored')] })
    const view = renderHook(() => useScenarioDatasetPreparation(input), {
      wrapper: ({ children }: { children: ReactNode }) => <StrictMode>{children}</StrictMode>,
    })
    await waitFor(() => expect(view.result.current.prepared).toBe(true))
    expect(load).toHaveBeenCalledTimes(1)
    expect(load).toHaveBeenCalledWith('generic', input.request)
    expect(onPrepared).toHaveBeenCalledTimes(1)
  })

  it('does not prepare providers again on technique, parameter, limit, filter or target edits', async () => {
    const pending = deferred<ScenarioDatasetLoadResponse>()
    load.mockReturnValueOnce(pending.promise)
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), { initialProps: options() })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    view.rerender(options(['missing'], { request: {
      techniques: ['another'], target_name: 'other', scenario_params: { max_turns: 2 },
      datasets: { sources: [{ name: 'missing', max_size: 1 }], max_total: 'all', filters: { data_types: ['text'] } },
    } }))
    await act(async () => { pending.resolve(loaded('missing')) })
    expect(view.result.current.prepared).toBe(true)
    view.rerender(options(['missing'], { request: { scenario_params: { max_turns: 3 } } }))
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('waits behind in-flight work, skips superseded selections, and ignores their completion', async () => {
    const first = deferred<ScenarioDatasetLoadResponse>()
    const current = deferred<ScenarioDatasetLoadResponse>()
    load.mockReturnValueOnce(first.promise).mockReturnValueOnce(current.promise)
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), { initialProps: options(['a']) })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    view.rerender(options(['a', 'b']))
    view.rerender(options(['b']))
    expect(load).toHaveBeenCalledTimes(1)
    await act(async () => { first.resolve(loaded('a')) })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(load.mock.calls[1]?.[1].datasets?.sources).toEqual([{ name: 'b' }])
    expect(view.result.current.prepared).toBe(false)
    expect(onPrepared).not.toHaveBeenCalled()
    await act(async () => { current.resolve(loaded('b')) })
    expect(view.result.current.prepared).toBe(true)
    expect(onPrepared).toHaveBeenCalledTimes(1)
  })

  it('does not trust late success or failure from an old runtime', async () => {
    const old = deferred<ScenarioDatasetLoadResponse>()
    const current = deferred<ScenarioDatasetLoadResponse>()
    load.mockReturnValueOnce(old.promise).mockReturnValueOnce(current.promise)
    const view = renderHook(() => useScenarioDatasetPreparation(options()))
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    generation = 'two'
    view.rerender()
    await act(async () => { old.reject(new Error('Old runtime failed')) })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(2))
    expect(view.result.current.error).toBeNull()
    expect(view.result.current.prepared).toBe(false)
    await act(async () => { current.resolve(loaded('missing')) })
    expect(view.result.current.prepared).toBe(true)
    expect(onPrepared).toHaveBeenCalledTimes(1)
  })

  it('shows failures, requires explicit Retry, and never resets the selection/request edits', async () => {
    load.mockRejectedValueOnce(new Error('No provider is available for this dataset.'))
    const input = options()
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), { initialProps: input })
    await waitFor(() => expect(view.result.current.error).toContain('No provider'))
    view.rerender({ ...input, request: { ...input.request, include_baseline: false } })
    expect(load).toHaveBeenCalledTimes(1)
    act(() => { view.result.current.retry() })
    await waitFor(() => expect(view.result.current.prepared).toBe(true))
    expect(load).toHaveBeenCalledTimes(2)
    expect(input.datasetNames).toEqual(['missing'])
  })

  it('does not fetch providers when only listing metadata or when required configuration is incomplete', () => {
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), {
      initialProps: options(['missing'], { metadataLoading: true }),
    })
    expect(view.result.current.prepared).toBe(false)
    view.rerender(options(['missing'], { metadataError: 'Catalog unavailable' }))
    view.rerender(options(['missing'], { request: null }))
    expect(load).not.toHaveBeenCalled()
  })

  it('rejects success-shaped replies that did not prepare the selected sources', async () => {
    load.mockResolvedValueOnce(loaded('other'))
    const view = renderHook(() => useScenarioDatasetPreparation(options()))
    await waitFor(() => expect(view.result.current.error).toContain('not stored after preparation: missing'))
    expect(view.result.current.prepared).toBe(false)
    expect(onPrepared).not.toHaveBeenCalled()
  })

  it('clears an obsolete preparation error when fresh metadata confirms the selected data is stored', async () => {
    load.mockRejectedValueOnce(new Error('Provider failed'))
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), { initialProps: options() })
    await waitFor(() => expect(view.result.current.error).toContain('Provider failed'))
    view.rerender(options(['missing'], { metadata: [stored('missing')] }))
    expect(view.result.current.prepared).toBe(true)
    expect(view.result.current.error).toBeNull()
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('ignores a completion after every source was removed', async () => {
    const pending = deferred<ScenarioDatasetLoadResponse>()
    load.mockReturnValueOnce(pending.promise)
    const view = renderHook((props: Options) => useScenarioDatasetPreparation(props), { initialProps: options() })
    await waitFor(() => expect(load).toHaveBeenCalledTimes(1))
    view.rerender(options([]))
    await act(async () => { pending.resolve(loaded('missing')) })
    expect(view.result.current.prepared).toBe(true)
    expect(onPrepared).not.toHaveBeenCalled()
  })
})
