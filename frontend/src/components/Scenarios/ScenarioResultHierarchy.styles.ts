import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScenarioResultHierarchyStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    minWidth: 0,
  },
  totals: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalL,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  group: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    paddingTop: tokens.spacingVerticalS,
    borderTop: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    minWidth: 0,
  },
  // Wide tables scroll inside their own box instead of widening the page on narrow screens.
  tableScroll: {
    width: '100%',
    overflowX: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  table: {
    minWidth: '52rem',
    tableLayout: 'auto',
  },
  resultCell: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    minWidth: '18rem',
  },
  outcomeCell: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalXXS,
  },
  nowrap: {
    whiteSpace: 'nowrap',
  },
  expandButton: {
    flexShrink: 0,
    ...mobileTouchTarget,
  },
  expandSpacer: {
    width: '2rem',
    flexShrink: 0,
    ...mobileTouchTarget,
  },
  position: {
    color: tokens.colorNeutralForeground3,
    fontVariantNumeric: 'tabular-nums',
  },
  objective: {
    minWidth: 0,
    overflowWrap: 'anywhere',
  },
  unresolved: {
    color: tokens.colorNeutralForeground3,
    fontStyle: 'italic',
    overflowWrap: 'anywhere',
  },
  footer: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    paddingTop: tokens.spacingVerticalXS,
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
})
