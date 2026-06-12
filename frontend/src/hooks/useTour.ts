import { useState, useCallback, useRef, useMemo, useEffect, createElement } from 'react'

import type { EventData } from 'react-joyride'
import { ACTIONS, LIFECYCLE, STATUS } from 'react-joyride'

import { TOUR_STEPS } from '../components/Tour/tourSteps'
import TourTooltip from '../components/Tour/TourTooltip'
import type { ViewName } from '../components/Sidebar/Navigation'

const STORAGE_KEY = 'pyrit-tour-completed'

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

  // Always-current ref so callbacks read the latest view without needing
  // currentView in their dependency arrays (which would cause Joyride to
  // see a new onEvent reference and potentially drop events).
  const currentViewRef = useRef(currentView)
  useEffect(() => {
    currentViewRef.current = currentView
  })

  // Holds the step index to advance to after a view switch completes.
  // null means "nothing pending". The useEffect below reads and clears this.
  const pendingStepRef = useRef<number | null>(null)

  // When currentView changes AND we have a pending step, the new view has
  // committed to the DOM. We use requestAnimationFrame to let the browser
  // paint one frame (so Joyride's target element is findable), then advance.
  useEffect(() => {
    // No pending step — this was a normal navigation, not a tour-driven one.
    if (pendingStepRef.current === null) return

    // Grab and clear the pending index so this only fires once.
    const nextIndex = pendingStepRef.current
    pendingStepRef.current = null

    // requestAnimationFrame waits for the browser to finish its next paint
    // cycle. After React commits the DOM (which triggered this useEffect),
    // there's a brief moment before the browser actually paints the pixels
    // and layout is finalized. rAF fires right after that paint, ensuring
    // Joyride can measure and find the target element's position.
    requestAnimationFrame(() => {
      setStepIndex(nextIndex)
      switchingViewRef.current = false
    })
  }, [currentView])

  const hasCompletedTour = localStorage.getItem(STORAGE_KEY) === 'true'

  const startTour = useCallback(() => {
    setStepIndex(0)
    // If we're already on home, start immediately.
    // Otherwise navigate and let the useEffect start after the view mounts.
    if (currentViewRef.current === 'home') {
      setRun(true)
    } else {
      pendingStepRef.current = 0
      switchingViewRef.current = true
      setRun(true)
      onNavigate('home')
    }
  }, [onNavigate])

  const endTour = useCallback(() => {
    setRun(false)
    setStepIndex(0)
    // Clear any pending step so the useEffect doesn't advance after cancel.
    pendingStepRef.current = null
    switchingViewRef.current = false
    onNavigate('home')
    localStorage.setItem(STORAGE_KEY, 'true')
  }, [onNavigate])

  const handleJoyrideEvent = useCallback((data: EventData) => {
    const { status, action, index, lifecycle } = data

    // Tour finished, user clicked skip, or user clicked the close (X) button
    if (
      status === STATUS.FINISHED ||
      status === STATUS.SKIPPED ||
      action === ACTIONS.CLOSE
    ) {
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
      // Stash the target step and navigate — the useEffect on currentView
      // will advance once React commits the new view's DOM.
      pendingStepRef.current = nextIndex
      switchingViewRef.current = true
      onNavigate(nextStep.viewRequired)
    } else {
      setStepIndex(nextIndex)
    }
  }, [onNavigate, endTour])

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
      locale: { back: 'Back', close: 'Close', last: "Anchors Away!", next: 'Next', skip: 'Skip tour' },
    },
  }
}
