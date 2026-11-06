import { makeStyles, tokens } from '@fluentui/react-components'
import {
  TOUCH_INPUT_QUERY,
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTarget,
  mobileTouchTargetHeight,
} from '../../styles/touchTargets'

import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useAttackHistoryStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
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
    marginBottom: tokens.spacingVerticalS,
  },
  filters: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'stretch',
    gap: tokens.spacingVerticalS,
  },
  filterRow: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
    flexWrap: 'wrap',
  },
  secondaryFilterRow: {
    display: 'flex',
    alignItems: 'center',
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
  },
  // Inner scrolling surface for the virtualized label list. The bound lives here
  // rather than on the listbox element because autoSize writes an inline max
  // height on the popup, which would override a class level limit.
  labelScrollSurface: {
    // A flex column keeps the virtualizer's spacer elements from collapsing
    // the visible height when the list shrinks to a few short rows, which
    // clipped the only option of a one match search by half its height.
    display: 'flex',
    flexDirection: 'column',
    maxHeight: '250px',
    overflowY: 'auto',
  },
  filterDropdown: {
    minWidth: '160px',
    ...mobileTouchTargetHeight,
    '& > input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    '& > [role="button"]': {
      [TOUCH_INPUT_QUERY]: {
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minWidth: MINIMUM_TOUCH_TARGET_SIZE,
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  touchTargetHeight: {
    ...mobileTouchTargetHeight,
  },
  content: {
    flex: 1,
    overflowY: 'auto',
    overflowX: 'auto',
  },
  table: {
    minWidth: '100%',
    tableLayout: 'auto' as const,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  colStatus: { minWidth: '100px', whiteSpace: 'nowrap' as const },
  colObjective: { minWidth: '240px', width: '35%' },
  colTarget: { minWidth: '120px', whiteSpace: 'nowrap' as const },
  colOperator: { minWidth: '120px', whiteSpace: 'nowrap' as const },
  colOperation: { minWidth: '130px', whiteSpace: 'nowrap' as const },
  colLabels: { minWidth: '180px', whiteSpace: 'nowrap' as const },
  colDate: { minWidth: '100px', whiteSpace: 'nowrap' as const },
  clickableRow: {
    cursor: 'pointer',
    backgroundColor: tokens.colorNeutralBackground2,
    ':hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '-2px',
    },
  },
  objectiveCell: {
    ...mobileTouchTarget,
    display: 'block',
    minWidth: 0,
    maxWidth: '100%',
    padding: 0,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightRegular,
    lineHeight: tokens.lineHeightBase200,
    textAlign: 'left',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  emptyValue: {
    color: tokens.colorNeutralForeground3,
  },
  nowrap: {
    whiteSpace: 'nowrap',
  },
  badgeGroup: {
    display: 'flex',
    gap: tokens.spacingHorizontalXXS,
    flexWrap: 'wrap',
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
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
    color: tokens.colorNeutralForeground3,
  },
})
