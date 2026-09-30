import { makeStyles, tokens } from '@fluentui/react-components'

import { WORKSPACE_CANVAS_PROPERTY } from '@/styles/workspaceBackground'

export const useThemeProviderStyles = makeStyles({
  decorated: {
    [WORKSPACE_CANVAS_PROPERTY]: 'transparent',
    '@media (forced-colors: active)': {
      [WORKSPACE_CANVAS_PROPERTY]: tokens.colorNeutralBackground2,
    },
  },
})
