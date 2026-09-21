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
        'History tracks past attacks, and Registry is where you manage targets and converters. Feel free to try clicking between these views!',
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
        ? 'This card shows your default objective target for new chats and scanner runs. Use Manage targets ' +
          'to change your objective or adversarial defaults in the Target Registry.'
        : 'Targets are the AI endpoints you test. Select a target from the Chat dropdown, or use the Target Registry ' +
          'to save objective and adversarial defaults for your account in this browser.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'home',
    },
    {
      target: hasActiveTarget
        ? '[data-tour="converter-toggle"]'
        : '[data-tour="chat-prerequisite"]',
      content: hasActiveTarget
        ? 'With a chat target selected, Chat shows the message composer. Use this Toggle converter panel button to transform text ' +
          'before sending, such as Base64 encoding or translation.'
        : 'Select a target from the Chat target dropdown to enable the message composer. If no targets are registered, ' +
          'choose Configure Target to create one. Saved chats automatically select their original registered target.',
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
