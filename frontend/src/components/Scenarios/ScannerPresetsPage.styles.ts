import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScannerPresetsPageStyles = makeStyles({
  cards: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  emptyHint: {
    color: tokens.colorNeutralForeground3,
  },
})
