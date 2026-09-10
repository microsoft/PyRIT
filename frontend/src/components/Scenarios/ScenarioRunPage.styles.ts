import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
  mobileTouchTarget,
} from '@/styles/touchTargets'

export const useScenarioRunPageStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    height: '100%',
    minWidth: 0,
    overflowY: 'auto',
    overflowX: 'hidden',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  content: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    maxWidth: '96rem',
    gap: tokens.spacingVerticalXL,
    padding: tokens.spacingVerticalXXL,
    marginInline: 'auto',
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
      gap: tokens.spacingVerticalL,
    },
  },
  backLink: {
    display: 'inline-flex',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: tokens.spacingHorizontalXS,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
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
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalXL,
    [NARROW_VIEWPORT_QUERY]: {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  headerIdentity: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    gap: tokens.spacingVerticalXS,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  runId: {
    color: tokens.colorNeutralForeground3,
    overflowWrap: 'anywhere',
  },
  headerActions: {
    display: 'flex',
    flexShrink: 0,
    gap: tokens.spacingHorizontalS,
    [NARROW_VIEWPORT_QUERY]: {
      width: '100%',
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  wideButton: {
    [NARROW_VIEWPORT_QUERY]: {
      flexGrow: 1,
    },
  },
  metadata: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(10rem, 1fr))',
    gap: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalXL}`,
    paddingTop: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: '1fr',
    },
  },
  metadataItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  metadataLabel: {
    color: tokens.colorNeutralForeground3,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  sectionHeading: {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  objectivesHeading: {
    display: 'flex',
    alignItems: 'baseline',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  sectionHint: {
    color: tokens.colorNeutralForeground3,
  },
  progressSurface: {
    display: 'grid',
    gridTemplateColumns: 'minmax(14rem, 2fr) repeat(2, minmax(8rem, 1fr))',
    gap: tokens.spacingHorizontalXL,
    alignItems: 'center',
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: '1fr',
      gap: tokens.spacingVerticalM,
    },
  },
  progressPrimary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    minWidth: 0,
  },
  progressText: {
    display: 'flex',
    alignItems: 'baseline',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalM,
  },
  metric: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  metricLabel: {
    color: tokens.colorNeutralForeground3,
  },
  metricValue: {
    fontVariantNumeric: 'tabular-nums',
  },
  summaryGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(15rem, 1fr))',
    gap: tokens.spacingHorizontalM,
  },
  summaryItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    padding: tokens.spacingVerticalL,
    borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  summaryTitle: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
  },
  detailLinkButton: {
    ...mobileTouchTarget,
    justifyContent: 'flex-start',
    minWidth: 0,
    paddingInline: 0,
    color: tokens.colorBrandForegroundLink,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    ':hover': {
      textDecorationLine: 'underline',
    },
  },
  summaryStats: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, 1fr)',
    gap: tokens.spacingHorizontalS,
  },
  summaryStat: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  tableScroll: {
    width: '100%',
    overflowX: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  table: {
    minWidth: '64rem',
    tableLayout: 'auto',
  },
  highlightedTableHeader: {
    backgroundColor: tokens.colorNeutralBackground3,
  },
  objectiveSuccessColumn: {
    width: '12rem',
    whiteSpace: 'nowrap',
    textAlign: 'right',
  },
  attemptsTable: {
    minWidth: '64rem',
    tableLayout: 'auto',
  },
  displayGroupList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  displayGroup: {
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    overflow: 'hidden',
    backgroundColor: tokens.colorNeutralBackground1,
  },
  displayGroupSummary: {
    display: 'grid',
    gridTemplateColumns: `${MINIMUM_TOUCH_TARGET_SIZE} minmax(18rem, 1fr) minmax(30rem, 1.4fr)`,
    alignItems: 'center',
    gap: tokens.spacingHorizontalM,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    padding: tokens.spacingVerticalM,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: `${MINIMUM_TOUCH_TARGET_SIZE} 1fr`,
      gap: tokens.spacingVerticalS,
    },
  },
  expandButton: {
    ...mobileTouchTarget,
    alignSelf: 'center',
  },
  displayGroupIdentity: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    gap: tokens.spacingVerticalXXS,
    overflowWrap: 'anywhere',
  },
  displayGroupMetrics: {
    display: 'grid',
    gridTemplateColumns: 'repeat(4, minmax(6rem, 1fr))',
    gap: tokens.spacingHorizontalM,
    [NARROW_VIEWPORT_QUERY]: {
      gridColumn: '1 / -1',
      gridTemplateColumns: 'repeat(2, minmax(6rem, 1fr))',
    },
  },
  displayGroupMetric: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    whiteSpace: 'nowrap',
  },
  displayGroupPanel: {
    padding: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  nowrap: {
    whiteSpace: 'nowrap',
    fontVariantNumeric: 'tabular-nums',
  },
  preview: {
    display: 'block',
    maxWidth: '24rem',
    overflow: 'hidden',
    whiteSpace: 'nowrap',
    textOverflow: 'ellipsis',
  },
  clickableAttemptRow: {
    cursor: 'pointer',
    ':hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '-2px',
    },
  },
  objectiveLinkButton: {
    ...mobileTouchTarget,
    justifyContent: 'flex-start',
    width: '100%',
    paddingInline: 0,
    color: tokens.colorBrandForegroundLink,
    textAlign: 'left',
    textDecorationLine: 'none',
    ':hover': {
      textDecorationLine: 'underline',
    },
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalS,
    minHeight: '8rem',
    padding: tokens.spacingVerticalXXL,
    color: tokens.colorNeutralForeground3,
    textAlign: 'center',
  },
  centeredState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    minHeight: '18rem',
    textAlign: 'center',
  },
  loadingBlock: {
    width: 'min(42rem, 100%)',
  },
  dialogContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    overflowWrap: 'anywhere',
  },
  attackDetailDialogSurface: {
    width: 'min(92vw, 88rem)',
    maxWidth: '88rem',
    maxHeight: '90vh',
    boxSizing: 'border-box',
    overflow: 'hidden',
  },
  attackDetailDialogBody: {
    minHeight: 0,
    maxHeight: 'calc(90vh - 48px)',
  },
  attackDetailDialogContent: {
    minHeight: 0,
    overflowY: 'auto',
  },
  detailList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    marginTop: tokens.spacingVerticalXS,
  },
  badgeList: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
    marginTop: tokens.spacingVerticalXS,
  },
  seedPromptList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    marginTop: tokens.spacingVerticalS,
  },
  seedPrompt: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    padding: tokens.spacingVerticalM,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  objective: {
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  liveStatus: {
    position: 'absolute',
    width: '1px',
    height: '1px',
    overflow: 'hidden',
    clip: 'rect(0 0 0 0)',
    clipPath: 'inset(50%)',
    whiteSpace: 'nowrap',
  },
})
