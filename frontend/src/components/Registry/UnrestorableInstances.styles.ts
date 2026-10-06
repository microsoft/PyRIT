import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useUnrestorableInstancesStyles = makeStyles({
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    margin: `${tokens.spacingVerticalXS} 0 0`,
    padding: 0,
    listStyleType: 'none',
  },
  item: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
  },
  details: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  type: {
    color: tokens.colorNeutralForeground3,
  },
  reason: {
    overflowWrap: 'anywhere',
  },
  deleteButton: {
    flexShrink: 0,
    ...mobileTouchTarget,
  },
})
