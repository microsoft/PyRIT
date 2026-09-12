import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useScenarioQueueStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  heading: {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  list: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    margin: 0,
    padding: 0,
    listStyleType: 'none',
  },
  entry: {
    display: 'grid',
    gridTemplateColumns: 'auto minmax(0, 1fr) auto',
    alignItems: 'center',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'auto minmax(0, 1fr)',
    },
  },
  link: {
    display: 'flex',
    flexDirection: 'column',
    justifyContent: 'center',
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    minWidth: 0,
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    ':hover': {
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  runId: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    color: tokens.colorNeutralForeground3,
  },
  timestamp: {
    color: tokens.colorNeutralForeground3,
    whiteSpace: 'nowrap',
    [NARROW_VIEWPORT_QUERY]: {
      gridColumn: '2',
    },
  },
  empty: {
    color: tokens.colorNeutralForeground3,
  },
})
