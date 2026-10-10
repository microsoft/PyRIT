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

/** Controls which guidance the tour shows for state-dependent steps. */
export interface TourContext {
  /** Whether a default objective target is set for this browser. */
  readonly hasActiveTarget: boolean
  /** Whether the Configuration nav button is rendered for this user. */
  readonly canManageConfiguration: boolean
}

/** Builds tour guidance for the controls available in the current app state. */
export function createTourSteps({
  hasActiveTarget,
  canManageConfiguration,
}: TourContext): TourStep[] {
  return [
    {
      target: '[data-tour="sidebar-nav"]',
      content:
        'Ahoy! Welcome to Co-PyRIT! This is your main navigation panel. Home is your dashboard, Chat is where you send prompts, ' +
        'History tracks past attacks and scanner runs, Scanner launches full test campaigns, and Registry is where you manage ' +
        'targets and converters.' +
        (canManageConfiguration
          ? ' Configuration holds environment settings for your deployment.'
          : '') +
        ' Feel free to try clicking between these views!',
      placement: 'right-start',
      skipBeacon: true,
      viewRequired: 'home',
    },
    {
      target: '[data-tour="labels-card"]',
      content:
        'The labels bar stays available across views, including scanner setup. Set "operator", "operation", ' +
        'and other labels here before starting an attack or scan. Existing runs keep their original labels.',
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
        : 'Click Select a target in the chat ribbon to enable the message composer. If no targets are registered, ' +
          'open the Target Registry to create one. Saved chats automatically select their original registered target.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'chat',
    },
    {
      target: '[data-tour="scanner-catalog"]',
      content:
        'Scanner runs a whole campaign for you. Pick a scenario to sweep many attack techniques and datasets ' +
        'against a target in one run, instead of sending prompts one at a time in Chat.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'scenarios',
    },
    {
      target: '[data-tour="history-tabs"]',
      content:
        'Every run is logged here. The Attacks tab lists individual conversations and the Scanner tab lists ' +
        'scenario runs. Each tab has its own filters, such as outcome, converter type, or labels, so you can ' +
        'find exactly what you need!',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'history',
    },
    {
      target: '[data-tour="registry-tabs"]',
      content:
        'The Registry is your home base. Register targets and set your objective and adversarial defaults under ' +
        'Targets, and browse the converters you can apply to prompts under Converters.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'registry',
    },
  ]
}

export const TOUR_STEPS = createTourSteps({
  hasActiveTarget: false,
  canManageConfiguration: false,
})
