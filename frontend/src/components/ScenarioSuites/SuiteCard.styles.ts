import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useSuiteCardStyles = makeStyles({
  launch: {
    ...mobileTouchTarget,
  },
  secondaryText: {
    color: tokens.colorNeutralForeground3,
  },
  purpose: {
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase300,
  },
})
