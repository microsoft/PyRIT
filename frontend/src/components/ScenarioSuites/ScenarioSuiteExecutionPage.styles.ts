import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
  mobileTouchTarget,
} from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useScenarioSuiteExecutionPageStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    height: '100%',
    minWidth: 0,
    overflowY: 'auto',
    overflowX: 'hidden',
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
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
  executionId: {
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
  sectionHint: {
    color: tokens.colorNeutralForeground3,
  },
  summaryGrid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(10rem, 1fr))',
    gap: tokens.spacingHorizontalM,
  },
  summaryItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  summaryValue: {
    fontVariantNumeric: 'tabular-nums',
  },
  tableScroll: {
    width: '100%',
    overflowX: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  table: {
    minWidth: '62rem',
  },
  entryIdentity: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  entryMeta: {
    color: tokens.colorNeutralForeground3,
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
    overflowWrap: 'anywhere',
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
  dialogContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    overflowWrap: 'anywhere',
  },
  liveStatus: {
    minHeight: '1.25rem',
    color: tokens.colorNeutralForeground3,
  },
})
