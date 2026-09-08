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

/** Builds tour guidance for the controls available in the current target state. */
export function createTourSteps(hasActiveTarget: boolean): TourStep[] {
  return [
    {
      target: '[data-tour="sidebar-nav"]',
      content:
        'Ahoy! Welcome to Co-PyRIT! This is your main navigation panel. Home is your dashboard, Chat is where you send prompts, ' +
        'History tracks past attacks, and Targets is where you set up targets. Feel free to try clicking between these views!',
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
      content: hasActiveTarget
        ? 'This card shows the target currently active for Chat. To switch targets after the tour, choose Manage targets ' +
          'and use Set Active in Targets.'
        : 'Targets are the AI endpoints you\'re testing. This card only shows the current target; target selection happens ' +
          'in Targets. After the tour, choose Configure a target, then create or choose one and use Set Active there.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'home',
    },
    {
      target: hasActiveTarget
        ? '[data-tour="converter-toggle"]'
        : '[data-tour="chat-prerequisite"]',
      content: hasActiveTarget
        ? 'With a target active, Chat shows the message composer. Use this Toggle converter panel button to transform text ' +
          'before sending, such as Base64 encoding or translation.'
        : 'Chat needs an active target before the message composer is available. After the tour, choose Configure a target to ' +
          'create or activate one in Targets, then return to Chat. The message input and converter control appear once ' +
          'a target is active.',
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
}

export const TOUR_STEPS = createTourSteps(false)
