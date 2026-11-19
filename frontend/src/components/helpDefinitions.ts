export const HELP_DEFINITIONS = {
  objectiveTarget: 'The model or endpoint you are testing. It receives prompts and returns responses.',
  adversarialTarget: 'The model used for adversarial chat: writing attack prompts. The objective target receives the prompts being tested.',
  technique: 'A named attack setup that combines an attack algorithm with prompts, converters, and settings.',
  requestConverters: 'Transform prompts before they are sent to the target. Converters run in the order shown.',
  responseConverters: 'Transform target responses before scoring. Converters run in the order shown.',
  maxConcurrency: 'Limits how much work one scenario runs at the same time. Lower values reduce load on its targets.',
  maxRetries: 'Maximum retries for retryable failures during a scenario run. Retries can increase target calls and run time.',
  promptRepetitions: 'How many conversations receive the next prompt. Additional conversations copy the current history before sending.',
  privateTarget: 'Everyone can see and use a shared target. Only me keeps the target, and anything you build with it, visible and usable only by you.',
  targetAccount: "Shared targets are available to everyone. Yours are available only to you. The icon shows whether the provider accepted the target's credentials on the last request; there is none until the target is used.",
  credentialUpdates: "Whether the target's API key or request headers can be replaced later, through the API or pyrit_scan set-target-credential, without recreating it. Only you can update a private target; anyone signed in can update a shared one.",
} as const
