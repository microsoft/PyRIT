import { tokens } from '@fluentui/react-components'

export const WORKSPACE_CANVAS_PROPERTY = '--pyrit-workspace-canvas-background'

/** Only page canvases opt in; Fluent content-surface tokens remain opaque. */
export const WORKSPACE_CANVAS_BACKGROUND =
  `var(${WORKSPACE_CANVAS_PROPERTY}, ${tokens.colorNeutralBackground2})`
