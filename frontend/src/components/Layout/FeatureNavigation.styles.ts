import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useFeatureNavigationStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    flexShrink: 0,
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  tabs: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXL,
  },
  action: { ...mobileTouchTargetHeight },
})
