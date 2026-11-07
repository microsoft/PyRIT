import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useFindingTreeItemStyles = makeStyles({
  badge: { height: 'auto', minWidth: 0, maxWidth: '100%', whiteSpace: 'normal', overflowWrap: 'anywhere' },
  toggle: { ...mobileTouchTargetHeight, alignSelf: 'flex-start', marginTop: tokens.spacingVerticalS },
  // Sub-findings sit under a guide line, so the depth of a decomposition reads at a glance.
  children: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    marginTop: tokens.spacingVerticalM,
    paddingLeft: tokens.spacingHorizontalL,
    // Griffel mirrors left-side styles for right-to-left text.
    borderLeft: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
  },
  childList: { margin: 0, padding: 0, listStyleType: 'none' },
  button: { ...mobileTouchTarget },
})
