import type { FormEvent } from 'react'

import { act, renderHook } from '@testing-library/react'

import type { BuildRunRequestResult } from '@/components/Scenarios/scenarioLaunchRequest'
import { scenariosApi } from '@/services/api'
import type { ScenarioRunSummary } from '@/types'

import { useScenarioLaunch } from './useScenarioLaunch'

jest.mock('@/services/api', () => ({ scenariosApi: { startRun: jest.fn() } }))
const startRun = jest.mocked(scenariosApi.startRun)

const VALID: BuildRunRequestResult = { ok: true, request: { scenario_name: 'scenario', target_name: 'target' } }
const SUMMARY = { scenario_result_id: 'run-1' } as ScenarioRunSummary

interface Props {
  requestResult?: BuildRunRequestResult
  submitBlocked?: boolean
  launchBlocked?: boolean
}

function renderLaunch(initial: Props = {}) {
  const onLaunched = jest.fn()
  const view = renderHook(
    ({ requestResult = VALID, submitBlocked = false, launchBlocked = false }: Props) => useScenarioLaunch({
      requestResult, submitBlocked, launchBlocked, onLaunched,
    }),
    { initialProps: initial },
  )
  return { view, onLaunched }
}

function submitEvent(): FormEvent<HTMLFormElement> {
  return { preventDefault: jest.fn() } as unknown as FormEvent<HTMLFormElement>
}

describe('useScenarioLaunch', () => {
  beforeEach(() => startRun.mockReset())

  it('opens the confirmation only for a valid request', () => {
    const { view } = renderLaunch({ requestResult: { ok: false, error: 'Select a target.' } })
    act(() => view.result.current.submitForm(submitEvent()))
    expect(view.result.current.previewOpen).toBe(false)
    expect(view.result.current.validationError).toBe('Select a target.')

    view.rerender({ requestResult: VALID })
    act(() => view.result.current.submitForm(submitEvent()))
    expect(view.result.current.previewOpen).toBe(true)
    expect(view.result.current.validationError).toBeNull()
  })

  it('does not open the confirmation while submitting is blocked', () => {
    const { view } = renderLaunch({ submitBlocked: true })
    act(() => view.result.current.submitForm(submitEvent()))

    expect(view.result.current.previewOpen).toBe(false)
  })

  it('starts one run when confirm is pressed twice in the same tick', async () => {
    let finish: (summary: ScenarioRunSummary) => void = () => {}
    startRun.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    const { view, onLaunched } = renderLaunch()
    act(() => view.result.current.submitForm(submitEvent()))

    let first: Promise<void> = Promise.resolve()
    let second: Promise<void> = Promise.resolve()
    act(() => {
      first = view.result.current.confirmLaunch()
      second = view.result.current.confirmLaunch()
    })
    expect(view.result.current.submitting).toBe(true)
    await act(async () => {
      finish(SUMMARY)
      await Promise.all([first, second])
    })

    expect(startRun).toHaveBeenCalledTimes(1)
    expect(startRun).toHaveBeenCalledWith(VALID.ok ? VALID.request : undefined)
    expect(onLaunched).toHaveBeenCalledWith(SUMMARY)
    expect(view.result.current.previewOpen).toBe(false)
    expect(view.result.current.submitting).toBe(false)
  })

  it('keeps the form and shows the error when the launch fails', async () => {
    startRun.mockRejectedValue({ isAxiosError: true, response: { status: 409, data: { detail: 'Already running.' } } })
    const { view, onLaunched } = renderLaunch()
    act(() => view.result.current.submitForm(submitEvent()))

    await act(async () => { await view.result.current.confirmLaunch() })

    expect(onLaunched).not.toHaveBeenCalled()
    expect(view.result.current.apiError).toBe('Already running.')
    expect(view.result.current.previewOpen).toBe(false)
    expect(view.result.current.submitting).toBe(false)
  })

  it('does not start a run while launching is blocked', async () => {
    const { view } = renderLaunch({ launchBlocked: true })

    await act(async () => { await view.result.current.confirmLaunch() })

    expect(startRun).not.toHaveBeenCalled()
  })

  it('closes the confirmation for a request that became invalid', async () => {
    const { view } = renderLaunch()
    act(() => view.result.current.submitForm(submitEvent()))
    view.rerender({ requestResult: { ok: false, error: 'Select at least one technique.' } })

    await act(async () => { await view.result.current.confirmLaunch() })

    expect(startRun).not.toHaveBeenCalled()
    expect(view.result.current.previewOpen).toBe(false)
    expect(view.result.current.validationError).toBe('Select at least one technique.')
  })

  it('keeps the confirmation open while a launch is in flight', async () => {
    startRun.mockReturnValue(new Promise(() => {}))
    const { view } = renderLaunch()
    act(() => view.result.current.submitForm(submitEvent()))
    act(() => { void view.result.current.confirmLaunch() })

    act(() => view.result.current.dismissPreview())
    act(() => view.result.current.changePreviewOpen(false))

    expect(view.result.current.previewOpen).toBe(true)
  })

  it('closes the confirmation on cancel', () => {
    const { view } = renderLaunch()
    act(() => view.result.current.submitForm(submitEvent()))

    act(() => view.result.current.dismissPreview())

    expect(view.result.current.previewOpen).toBe(false)
    expect(startRun).not.toHaveBeenCalled()
  })
})
