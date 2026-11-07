import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useFindingPathsPanelStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  header: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalM },
  list: {
    margin: 0,
    paddingInlineStart: tokens.spacingHorizontalXL,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  path: { overflowWrap: 'anywhere' },
  pathHeader: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS },
  badge: { height: 'auto', minWidth: 0, whiteSpace: 'normal' },
  // Steps read left to right as one chain, and wrap on narrow screens.
  steps: {
    margin: 0,
    padding: 0,
    listStyleType: 'none',
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'baseline',
    columnGap: tokens.spacingHorizontalS,
    rowGap: tokens.spacingVerticalXXS,
    marginTop: tokens.spacingVerticalXXS,
  },
  step: { display: 'inline-flex', alignItems: 'baseline', gap: tokens.spacingHorizontalS, minWidth: 0 },
  arrow: { color: tokens.colorNeutralForeground3 },
  missing: { color: tokens.colorStatusWarningForeground1 },
  muted: { color: tokens.colorNeutralForeground3 },
  button: { ...mobileTouchTargetHeight },
})
