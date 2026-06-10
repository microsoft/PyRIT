import { useState, useCallback, useRef, useMemo, createElement } from 'react'

import type { EventData } from 'react-joyride'
import { ACTIONS, LIFECYCLE, STATUS } from 'react-joyride'

import { TOUR_STEPS } from '../components/Tour/tourSteps'
import TourTooltip from '../components/Tour/TourTooltip'
import type { ViewName } from '../components/Sidebar/Navigation'

const STORAGE_KEY = 'pyrit-tour-completed'

/** Milliseconds to wait after a view switch so the new DOM is ready. */
const VIEW_SWITCH_DELAY_MS = 400

/**
 * Manages the onboarding tour lifecycle: step progression, cross-view
 * navigation, and localStorage persistence.
 *
 * Returns props to spread onto `<Joyride>` plus control functions.
 */
export function useTour(onNavigate: (view: ViewName) => void, isDarkMode: boolean, currentView: ViewName) {
  const [run, setRun] = useState(false)
  const [stepIndex, setStepIndex] = useState(0)

  // Ref to track whether we're in the middle of a delayed view switch.
  // Prevents double-advancing if the user clicks rapidly.
  const switchingViewRef = useRef(false)

  // Always-current ref of the active view so the callback reads the real
  // value, not a stale closure capture.
  const currentViewRef = useRef(currentView)
  currentViewRef.current = currentView

  const hasCompletedTour = localStorage.getItem(STORAGE_KEY) === 'true'

  const startTour = useCallback(() => {
    onNavigate('home')
    setStepIndex(0)
    // Small delay so the home view mounts before Joyride looks for targets.
    setTimeout(() => setRun(true), VIEW_SWITCH_DELAY_MS)
  }, [onNavigate])

  const endTour = useCallback(() => {
    setRun(false)
    setStepIndex(0)
    onNavigate('home')
    localStorage.setItem(STORAGE_KEY, 'true')
  }, [])

  const handleJoyrideEvent = useCallback((data: EventData) => {
    const { status, action, index, lifecycle } = data

    // Tour finished, user clicked skip, or user clicked the close (X) button
    if (
      status === STATUS.FINISHED ||
      status === STATUS.SKIPPED ||
      action === ACTIONS.CLOSE
    ) {
      console.log('Tour ended with status:', status, 'and action:', action)
      endTour()
      return
    }

    // We only care about the moment a step is fully dismissed (lifecycle complete)
    if (lifecycle !== LIFECYCLE.COMPLETE) {
      return
    }

    // Prevent double-advance during a view switch delay
    if (switchingViewRef.current) {
      return
    }

    const nextIndex = index + (action === ACTIONS.PREV ? -1 : 1)

    // Past end final index means the tour is complete
    if (nextIndex >= TOUR_STEPS.length) {
      endTour()
      return
    }

    // Shouldn't happen, but guard against negative index
    if (nextIndex < 0) {
      return
    }

    const nextStep = TOUR_STEPS[nextIndex]

    if (nextStep.viewRequired !== currentViewRef.current) {
      // The required view differs from the actual current view.
      // Navigate first, then advance after a delay so React can mount.
      switchingViewRef.current = true
      onNavigate(nextStep.viewRequired)
      setTimeout(() => {
        setStepIndex(nextIndex)
        switchingViewRef.current = false
      }, VIEW_SWITCH_DELAY_MS)
    } else {
      setStepIndex(nextIndex)
    }
  }, [onNavigate])

  // Wrap TourTooltip so it receives isDarkMode via closure.
  // Uses createElement instead of JSX because this is a .ts file (not .tsx).
  // Memoized so Joyride doesn't see a new component reference every render.
  const tooltip = useMemo(
    () => function WrappedTourTooltip(props: Parameters<typeof TourTooltip>[0]) {
      return createElement(TourTooltip, { ...props, isDarkMode })
    },
    [isDarkMode],
  )

  return {
    /** Call to start (or restart) the tour from step 1 on the Home view. */
    startTour,
    /** Whether the user has completed the tour at least once. */
    hasCompletedTour,
    /** Props to spread onto the `<Joyride>` component. */
    tourProps: {
      steps: [...TOUR_STEPS],
      run,
      stepIndex,
      onEvent: handleJoyrideEvent,
      continuous: true,
      showSkipButton: true,
      spotlightClicks: false,
      tooltipComponent: tooltip,
      floatingOptions: { hideArrow: true },
      // Make the close (X) button skip the entire tour instead of advancing.
      // Without this, Joyride's default 'close' action advances to the next
      // step internally before our onEvent fires, causing the view to snap.
      options: {
        closeButtonAction: 'skip' as const,
        overlayClickAction: false as const,
      },
      locale: { back: 'Back', close: 'Close', last: "Let's go!", next: 'Next', skip: 'Skip tour' },
    },
  }
}
