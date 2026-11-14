import { makeStyles, tokens } from '@fluentui/react-components'

import { NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useDetailFactsStyles = makeStyles({
  facts: {
    display: 'grid',
    gridTemplateColumns: '9rem minmax(0, 1fr)',
    alignItems: 'baseline',
    gap: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalXL}`,
    margin: 0,
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase300,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
      rowGap: tokens.spacingVerticalXS,
    },
  },
  primaryLabel: {
    margin: 0,
    color: tokens.colorNeutralForeground1,
  },
  secondaryLabel: {
    margin: 0,
    color: tokens.colorNeutralForeground3,
  },
  value: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalS,
    minWidth: 0,
    margin: 0,
    overflowWrap: 'anywhere',
    [NARROW_VIEWPORT_QUERY]: {
      paddingBottom: tokens.spacingVerticalM,
    },
  },
})
