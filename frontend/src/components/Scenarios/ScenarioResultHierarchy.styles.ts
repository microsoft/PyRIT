import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScenarioResultHierarchyStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM, minWidth: 0 },
  hint: { color: tokens.colorNeutralForeground3 },
  footer: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  touchTarget: { ...mobileTouchTarget },
})
