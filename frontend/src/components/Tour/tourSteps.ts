import type { Step } from 'react-joyride'

import type { ViewName } from '../Sidebar/Navigation'

/**
 * Extended step type that includes which view must be active
 * for the step's target element to exist in the DOM.
 */
export interface TourStep extends Step {
  /** The view that must be active before this step renders. */
  readonly viewRequired: ViewName
}

export const TOUR_STEPS: readonly TourStep[] = [
  {
    target: '[data-tour="sidebar-nav"]',
    content:
      'Ahoy! Welcome to Co-PyRIT! This is your main navigation panel. Home is your dashboard, Chat is where you send prompts, ' +
      'History tracks past attacks, and Configuration is where you set up targets. Feel free to try clicking between these views!',
    placement: 'right-start',
    skipBeacon: true,
    viewRequired: 'home',
  },
  {
    target: '[data-tour="labels-card"]',
    content:
      'Labels like "operator" and "operation" tag every attack you run, making them easy to find later. ' +
      'Update the defaults before you start!',
    placement: 'bottom',
    skipBeacon: true,
    viewRequired: 'home',
  },
  {
    target: '[data-tour="target-card"]',
    content:
      'Targets are the AI endpoints you\'re testing. Head to Configuration to set one up, ' +
      'then come back here to select it before chatting.',
    placement: 'bottom',
    skipBeacon: true,
    viewRequired: 'home',
  },
  {
    target: '[data-tour="chat-area"]',
    content:
      'This is where you send prompts and view assistant responses. Use the converter panel toggle on the ' +
      'left of the message input box to transform your text before sending — like Base64 encoding or translation.',
    placement: 'bottom',
    skipBeacon: true,
    viewRequired: 'chat',
  },
  {
    target: '[data-tour="history-filters"]',
    content:
      'Every attack is logged here. Filter by different criteria like outcome, converter type, or labels to ' +
      'find exactly what you need!',
    placement: 'bottom',
    skipBeacon: true,
    viewRequired: 'history',
  },
]
