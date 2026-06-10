import { renderHook, act } from '@testing-library/react'

import { ACTIONS, LIFECYCLE, STATUS } from 'react-joyride'

import { useTour } from './useTour'
import { TOUR_STEPS } from '../components/Tour/tourSteps'

const STORAGE_KEY = 'pyrit-tour-completed'

// Minimal EventData shape — only fields our handler reads
function makeEvent(overrides: Record<string, unknown> = {}) {
  return {
    action: ACTIONS.NEXT,
    controlled: true,
    index: 0,
    lifecycle: LIFECYCLE.COMPLETE,
    origin: null,
    size: TOUR_STEPS.length,
    status: STATUS.RUNNING,
    step: TOUR_STEPS[0],
    error: null,
    scroll: null,
    scrolling: false,
    waiting: false,
    ...overrides,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any
}

describe('useTour', () => {
  const onNavigate = jest.fn()

  beforeEach(() => {
    jest.useFakeTimers()
    jest.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => {
    jest.useRealTimers()
  })

  it('returns hasCompletedTour=false when localStorage is empty', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))
    expect(result.current.hasCompletedTour).toBe(false)
  })

  it('returns hasCompletedTour=true when localStorage flag is set', () => {
    localStorage.setItem(STORAGE_KEY, 'true')
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))
    expect(result.current.hasCompletedTour).toBe(true)
  })

  it('startTour navigates to home and sets run=true after delay', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    expect(onNavigate).toHaveBeenCalledWith('home')
    // run is still false before the timer fires
    expect(result.current.tourProps.run).toBe(false)

    act(() => { jest.advanceTimersByTime(400) })

    expect(result.current.tourProps.run).toBe(true)
    expect(result.current.tourProps.stepIndex).toBe(0)
  })

  it('advances stepIndex on LIFECYCLE.COMPLETE + ACTIONS.NEXT (same view)', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    // Start the tour
    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    // Simulate Next on step 0 (home → home, no view switch)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 0,
        lifecycle: LIFECYCLE.COMPLETE,
      }))
    })

    expect(result.current.tourProps.stepIndex).toBe(1)
    // No extra navigation call beyond the initial startTour 'home'
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('goes back on ACTIONS.PREV', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    // Advance to step 1
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 0 }))
    })
    expect(result.current.tourProps.stepIndex).toBe(1)

    // Go back to step 0
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.PREV, index: 1 }))
    })
    expect(result.current.tourProps.stepIndex).toBe(0)
  })

  it('stops tour on ACTIONS.CLOSE and persists to localStorage', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })
    expect(result.current.tourProps.run).toBe(true)

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.CLOSE }))
    })

    expect(result.current.tourProps.run).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')
  })

  it('stops tour on STATUS.SKIPPED', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ status: STATUS.SKIPPED }))
    })

    expect(result.current.tourProps.run).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')
  })

  it('stops tour on STATUS.FINISHED', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ status: STATUS.FINISHED }))
    })

    expect(result.current.tourProps.run).toBe(false)
  })

  it('navigates to correct view when crossing view boundaries', () => {
    // Step 2 (index 2) is home, step 3 (index 3) is chat
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })
    onNavigate.mockClear()

    // Simulate Next on step 2 (last home step → chat step)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 2,
      }))
    })

    expect(onNavigate).toHaveBeenCalledWith('chat')

    // stepIndex advances after the delay
    act(() => { jest.advanceTimersByTime(400) })
    expect(result.current.tourProps.stepIndex).toBe(3)
  })

  it('navigates when user manually switched views (currentView differs from step)', () => {
    // User is on step 0 (viewRequired: 'home') but manually switched to 'chat'
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'chat' as const } },
    )

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })
    onNavigate.mockClear()

    // Step 1 (index 1) requires 'home', but currentView is 'chat'
    // so it should navigate to 'home'
    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 0,
      }))
    })

    expect(onNavigate).toHaveBeenCalledWith('home')
    act(() => { jest.advanceTimersByTime(400) })
    expect(result.current.tourProps.stepIndex).toBe(1)
  })

  it('ignores events with lifecycle !== COMPLETE', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        lifecycle: LIFECYCLE.READY, // not COMPLETE
      }))
    })

    // Step should not advance
    expect(result.current.tourProps.stepIndex).toBe(0)
  })

  it('prevents double-advance during view switch delay', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })
    onNavigate.mockClear()

    // Trigger a cross-view advance (step 2 → step 3, home → chat)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 2 }))
    })

    // While the delay is pending, fire another event
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 2 }))
    })

    // onNavigate should only have been called once
    expect(onNavigate).toHaveBeenCalledTimes(1)

    act(() => { jest.advanceTimersByTime(400) })
    expect(result.current.tourProps.stepIndex).toBe(3)
  })

  it('does not advance past the last step', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'history'))
    const lastIndex = TOUR_STEPS.length - 1

    act(() => { result.current.startTour() })
    act(() => { jest.advanceTimersByTime(400) })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: lastIndex,
      }))
    })

    // Tour should end, not advance
    expect(result.current.tourProps.run).toBe(false)
    expect(localStorage.getItem(STORAGE_KEY)).toBe('true')
  })
})
