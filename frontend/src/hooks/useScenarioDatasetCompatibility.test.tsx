import { act, renderHook } from '@testing-library/react'

import { scenariosApi } from '@/services/api'
import type { ScenarioDatasetCompatibility, ScenarioRunSizeEstimateRequest } from '@/types'

import { useScenarioDatasetCompatibility } from './useScenarioDatasetCompatibility'

jest.mock('@/services/api', () => ({ scenariosApi: { assessDatasets: jest.fn() } }))
const assess = jest.mocked(scenariosApi.assessDatasets)
const RESULT: ScenarioDatasetCompatibility = {
  requirements: [], items: [], selection_compatible: true, selection_reasons: [],
}

interface Props {
  request: ScenarioRunSizeEstimateRequest | null
  revision: string
  ready: boolean
}

function renderAssessment(initial: Props) {
  return renderHook(
    (props: Props) => useScenarioDatasetCompatibility({ ...props, scenarioName: 'policy' }),
    { initialProps: initial },
  )
}

async function settle(): Promise<void> {
  await act(async () => { jest.advanceTimersByTime(250) })
}

describe('useScenarioDatasetCompatibility', () => {
  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
    assess.mockResolvedValue(RESULT)
  })
  afterEach(() => { jest.useRealTimers() })

  it('assesses selected contents without loading or generating a provider', async () => {
    const request = { techniques: ['one'], datasets: { sources: [{ name: 'selected' }] } }
    const view = renderAssessment({ request, revision: 'one', ready: true })
    expect(assess).not.toHaveBeenCalled()
    await settle()
    expect(assess.mock.calls[0]?.slice(0, 3)).toEqual(['policy', request, true])
    expect(view.result.current.data?.selection_compatible).toBe(true)
  })

  it('does not revalidate when an equivalent request is recreated during a rerender', async () => {
    const request = { techniques: ['one'], datasets: { sources: [{ name: 'selected' }] } }
    const view = renderAssessment({ request, revision: 'one', ready: true })
    await settle()
    view.rerender({ request: { ...request }, revision: 'one', ready: true })
    await settle()
    expect(assess).toHaveBeenCalledTimes(1)
  })

  it('does not reuse compatibility after technique edits or a dataset metadata refresh', async () => {
    const first = { techniques: ['one'], datasets: { sources: [{ name: 'selected' }] } }
    const view = renderAssessment({ request: first, revision: 'one', ready: true })
    await settle()
    expect(view.result.current.data).toEqual(RESULT)
    const second = { ...first, techniques: ['two'] }
    view.rerender({ request: second, revision: 'one', ready: true })
    expect(view.result.current.data).toBeNull()
    expect(view.result.current.loading).toBe(true)
    await settle()
    expect(assess.mock.calls[1]?.[1].datasets).toEqual(first.datasets)
    view.rerender({ request: second, revision: 'changed-data', ready: true })
    expect(view.result.current.data).toBeNull()
    await settle()
    expect(assess).toHaveBeenCalledTimes(3)
  })

  it('ignores a stale response and surfaces current assessment failures', async () => {
    let finish: (value: ScenarioDatasetCompatibility) => void = () => {}
    assess.mockReturnValueOnce(new Promise((resolve) => { finish = resolve }))
    const view = renderAssessment({ request: { techniques: ['one'] }, revision: 'one', ready: true })
    await settle()
    assess.mockRejectedValueOnce(new Error('Cannot assess data'))
    view.rerender({ request: { techniques: ['two'] }, revision: 'one', ready: true })
    await settle()
    await act(async () => { finish(RESULT) })
    expect(view.result.current.data).toBeNull()
    expect(view.result.current.error).toContain('Cannot assess data')
  })

  it('waits for a valid request and ready runtime', async () => {
    const view = renderAssessment({ request: null, revision: 'one', ready: true })
    await settle()
    view.rerender({ request: {}, revision: 'one', ready: false })
    await settle()
    expect(assess).not.toHaveBeenCalled()
    expect(view.result.current.loading).toBe(false)
  })
})
