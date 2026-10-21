import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAnalyticsStatsStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'stretch',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXL,
    paddingBlock: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    [NARROW_VIEWPORT_QUERY]: { gap: tokens.spacingHorizontalM },
  },
  metric: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    justifyContent: 'center',
  },
  number: { fontVariantNumeric: 'tabular-nums' },
  outcomes: {
    display: 'flex',
    flex: 1,
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  button: { columnGap: tokens.spacingHorizontalXS, ...mobileTouchTargetHeight },
  asrLabel: {
    paddingInline: tokens.spacingHorizontalXS,
    minWidth: 0,
    justifyContent: 'flex-start',
    ...mobileTouchTargetHeight,
  },
})
