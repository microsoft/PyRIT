import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  TOUCH_INPUT_QUERY,
  mobileTouchTarget,
  mobileTouchTargetHeight,
} from '@/styles/touchTargets'

export const useScenarioHistoryStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  header: {
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalXXL}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  headerRow: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    marginTop: tokens.spacingVerticalS,
  },
  filterDropdown: {
    minWidth: '160px',
    ...mobileTouchTargetHeight,
    '& > input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  content: {
    flex: 1,
    overflow: 'auto',
  },
  table: {
    minWidth: '1120px',
  },
  clickableRow: {
    cursor: 'pointer',
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    ':hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
  },
  rowLink: {
    color: 'inherit',
    display: 'inline-flex',
    alignItems: 'center',
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    textDecorationLine: 'none',
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  identity: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: '180px',
  },
  secondary: {
    color: tokens.colorNeutralForeground3,
  },
  nowrap: {
    whiteSpace: 'nowrap',
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXS,
    maxWidth: '240px',
  },
  target: {
    display: 'flex',
    flexDirection: 'column',
    maxWidth: '220px',
  },
  truncate: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
  },
  pagination: {
    display: 'flex',
    justifyContent: 'center',
    alignItems: 'center',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalXXL}`,
    borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  touchTargetHeight: {
    ...mobileTouchTargetHeight,
  },
})
