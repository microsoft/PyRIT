import { renderHook, act } from '@testing-library/react'

import { ACTIONS, LIFECYCLE, STATUS } from 'react-joyride'

import { useTour } from './useTour'
import { TOUR_STEPS } from '../components/Tour/tourSteps'
import type { ViewName } from '../components/Sidebar/Navigation'

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
    jest.clearAllMocks()
    localStorage.clear()
    // Mock requestAnimationFrame — jsdom doesn't implement it.
    // Call the callback synchronously so tests don't need to wait.
    jest.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
      cb(0)
      return 0
    })
  })

  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('startTour sets run=true immediately when already on home', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    expect(result.current.tourProps.run).toBe(true)
    expect(result.current.tourProps.stepIndex).toBe(0)
    expect(result.current.tourProps.floatingOptions).toEqual({
      hideArrow: true,
      shiftOptions: {
        boundary: [],
        crossAxis: true,
        padding: 12,
        rootBoundary: 'viewport',
      },
    })
  })

  it('uses visible setup guidance when no target is active', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home', false))
    const steps = result.current.tourProps.steps

    expect(steps[2].content).toContain('target selection happens in Targets')
    expect(steps[2].content).toContain('choose Configure a target')
    expect(steps[2].content).toContain('use Set Active there')
    expect(steps[3].target).toBe('[data-tour="chat-prerequisite"]')
    expect(steps[3].content).toContain('before the message composer is available')
    expect(steps[3].content).toContain('converter control appear once a target is active')
  })

  it('uses the active target card and visible converter control when a target is active', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home', true))
    const steps = result.current.tourProps.steps

    expect(steps[2].content).toContain('target currently active for Chat')
    expect(steps[2].content).toContain('after the tour')
    expect(steps[2].content).toContain('use Set Active in Targets')
    expect(steps[3].target).toBe('[data-tour="converter-toggle"]')
    expect(steps[3].content).toContain('Chat shows the message composer')
    expect(steps[3].content).toContain('Toggle converter panel')
  })

  it('keeps tour controls available after manual navigation away from the current step', () => {
    const { result, rerender } = renderHook(
      ({ currentView }: { currentView: ViewName }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'home' as ViewName } },
    )

    act(() => { result.current.startTour() })
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ index: 0 }))
    })
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ index: 1 }))
    })
    expect(result.current.tourProps.stepIndex).toBe(2)

    rerender({ currentView: 'targets' })

    expect(result.current.tourProps.run).toBe(true)
    expect(result.current.tourProps.steps[2]).toMatchObject({
      target: 'body',
      placement: 'center',
      hideOverlay: true,
      disableFocusTrap: true,
    })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.PREV,
        index: 2,
      }))
    })
    expect(onNavigate).toHaveBeenCalledWith('home')

    rerender({ currentView: 'home' })
    expect(result.current.tourProps.stepIndex).toBe(1)
    expect(result.current.tourProps.steps[1].target).toBe('[data-tour="labels-card"]')
  })

  it('adapts the next step when a target is activated from another view', () => {
    interface HookProps {
      currentView: ViewName
      hasActiveTarget: boolean
    }

    const { result, rerender } = renderHook(
      ({ currentView, hasActiveTarget }: HookProps) =>
        useTour(onNavigate, true, currentView, hasActiveTarget),
      {
        initialProps: {
          currentView: 'home' as ViewName,
          hasActiveTarget: false,
        },
      },
    )

    act(() => { result.current.startTour() })
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ index: 0 }))
    })
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ index: 1 }))
    })

    rerender({ currentView: 'targets', hasActiveTarget: false })
    expect(result.current.tourProps.steps[2].content).toContain('Configure a target')

    rerender({ currentView: 'targets', hasActiveTarget: true })
    expect(result.current.tourProps.steps[2].target).toBe('body')
    expect(result.current.tourProps.steps[2].content).toContain('target currently active for Chat')
    expect(result.current.tourProps.steps[3].target).toBe('[data-tour="converter-toggle"]')

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ index: 2 }))
    })
    expect(onNavigate).toHaveBeenCalledWith('chat')

    rerender({ currentView: 'chat', hasActiveTarget: true })
    expect(result.current.tourProps.stepIndex).toBe(3)
    expect(result.current.tourProps.steps[3].target).toBe('[data-tour="converter-toggle"]')
    expect(result.current.tourProps.steps[3].hideOverlay).toBeUndefined()
  })

  it('startTour navigates to home and defers step when on a different view', () => {
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'chat' as const } },
    )

    act(() => { result.current.startTour() })

    expect(onNavigate).toHaveBeenCalledWith('home')
    expect(result.current.tourProps.run).toBe(true)

    // Simulate App changing currentView after onNavigate
    rerender({ currentView: 'home' as const })

    expect(result.current.tourProps.stepIndex).toBe(0)
  })

  it('advances stepIndex on LIFECYCLE.COMPLETE + ACTIONS.NEXT (same view)', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 0,
        lifecycle: LIFECYCLE.COMPLETE,
      }))
    })

    expect(result.current.tourProps.stepIndex).toBe(1)
  })

  it('goes back on ACTIONS.PREV', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

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

  it('stops tour on ACTIONS.CLOSE', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })
    expect(result.current.tourProps.run).toBe(true)

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.CLOSE }))
    })
    expect(result.current.tourProps.run).toBe(false)
  })

  it('stops tour on STATUS.SKIPPED', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ status: STATUS.SKIPPED }))
    })
    expect(result.current.tourProps.run).toBe(false)
  })

  it('stops tour on STATUS.FINISHED', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({ status: STATUS.FINISHED }))
    })

    expect(result.current.tourProps.run).toBe(false)
  })

  it('navigates to correct view when crossing view boundaries', () => {
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'home' as const } },
    )

    act(() => { result.current.startTour() })
    onNavigate.mockClear()

    // Simulate Next on step 2 (last home step → chat step)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 2,
      }))
    })

    expect(onNavigate).toHaveBeenCalledWith('chat')

    // Simulate App reacting to onNavigate by changing currentView
    rerender({ currentView: 'chat' as const })

    // useEffect fires → rAF fires → stepIndex advances
    expect(result.current.tourProps.stepIndex).toBe(3)
  })

  it('navigates when user manually switched views (currentView differs from step)', () => {
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'chat' as const } },
    )

    act(() => { result.current.startTour() })

    // Simulate App changing currentView to 'home' after startTour's onNavigate
    rerender({ currentView: 'home' as const })
    onNavigate.mockClear()

    // Step 1 (index 1) requires 'home' — currentView is now 'home' (same view)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: 0,
      }))
    })

    expect(result.current.tourProps.stepIndex).toBe(1)
  })

  it('ignores events with lifecycle !== COMPLETE', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))

    act(() => { result.current.startTour() })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        lifecycle: LIFECYCLE.READY,
      }))
    })

    expect(result.current.tourProps.stepIndex).toBe(0)
  })

  it('prevents double-advance during view switch', () => {
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'home' as const } },
    )

    act(() => { result.current.startTour() })
    onNavigate.mockClear()

    // Trigger a cross-view advance (step 2 → step 3, home → chat)
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 2 }))
    })

    // While the switch is pending (before rerender), fire another event
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 2 }))
    })

    // onNavigate should only have been called once
    expect(onNavigate).toHaveBeenCalledTimes(1)

    // Now simulate the view change completing
    rerender({ currentView: 'chat' as const })
    expect(result.current.tourProps.stepIndex).toBe(3)
  })

  it('does not advance past the last step', () => {
    const { result } = renderHook(() => useTour(onNavigate, true, 'home'))
    const lastIndex = TOUR_STEPS.length - 1

    act(() => { result.current.startTour() })

    act(() => {
      result.current.tourProps.onEvent(makeEvent({
        action: ACTIONS.NEXT,
        index: lastIndex,
      }))
    })
    expect(result.current.tourProps.run).toBe(false)
  })

  it('clears pending step when tour is cancelled during view switch', () => {
    const { result, rerender } = renderHook(
      ({ currentView }) => useTour(onNavigate, true, currentView),
      { initialProps: { currentView: 'home' as const } },
    )

    act(() => { result.current.startTour() })

    // Trigger cross-view advance
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.NEXT, index: 2 }))
    })

    // Cancel the tour before the view switch completes
    act(() => {
      result.current.tourProps.onEvent(makeEvent({ action: ACTIONS.CLOSE }))
    })

    // Now simulate the view changing — should NOT advance because tour was cancelled
    rerender({ currentView: 'chat' as const })
    expect(result.current.tourProps.run).toBe(false)
    expect(result.current.tourProps.stepIndex).toBe(0)
  })
})
