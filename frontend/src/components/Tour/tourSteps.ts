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
  /** Whether a last-used objective target is available for this browser. */
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
        'Ahoy! Welcome to Co-PyRIT! This is your main navigation panel. Home is your dashboard, Attacks opens Chat, ' +
        'Scanner launches full test campaigns, and Registry is where you manage ' +
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
        ? 'This card shows your last-used objective target for new chats and scanner runs. Use Manage targets ' +
          'to register targets or change your adversarial default in the Target Registry.'
        : 'Targets are the AI endpoints you test. Select a target from the Chat dropdown, or use the Target Registry ' +
          'to register targets and set an adversarial default for your account in this browser.',
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
      target: '[data-tour="feature-tabs"]',
      content:
        'Attacks and Scanner each have a History tab. Use Chat to return to your current conversation, ' +
        'or New attack to start a new one. Scanner history lists scenario runs and accepts an ID search.',
      placement: 'bottom',
      skipBeacon: true,
      viewRequired: 'chat',
    },
    {
      target: '[data-tour="registry-tabs"]',
      content:
        'The Registry is your home base. Register targets and set your adversarial default under ' +
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
