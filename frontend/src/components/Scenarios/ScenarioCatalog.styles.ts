import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTarget,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useScenarioCatalogStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    minWidth: 0,
    padding: tokens.spacingVerticalXXL,
    overflowX: 'hidden',
    overflowY: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
    [NARROW_VIEWPORT_QUERY]: {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingVerticalL,
    marginBottom: tokens.spacingVerticalXL,
    [NARROW_VIEWPORT_QUERY]: {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  headerText: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  subtitle: {
    color: tokens.colorNeutralForeground3,
  },
  explanation: {
    maxWidth: '75ch',
    margin: `${tokens.spacingVerticalS} 0 0`,
    color: tokens.colorNeutralForeground2,
  },
  headerActions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
    [NARROW_VIEWPORT_QUERY]: {
      width: '100%',
    },
  },
  search: {
    minWidth: '16rem',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
      flex: 1,
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
  },
  centeredState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalXXXL,
    textAlign: 'center',
    color: tokens.colorNeutralForeground3,
  },
  tableContainer: {
    minWidth: 0,
    overflowX: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      overflowX: 'visible',
      border: 0,
      borderRadius: 0,
      backgroundColor: 'transparent',
    },
  },
  table: {
    width: '100%',
    minWidth: '52rem',
    tableLayout: 'fixed',
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
      minWidth: 0,
    },
  },
  tableHeader: {
    position: 'sticky',
    top: 0,
    zIndex: 1,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      position: 'absolute',
      width: '1px',
      height: '1px',
      padding: 0,
      margin: '-1px',
      overflow: 'hidden',
      clip: 'rect(0, 0, 0, 0)',
      whiteSpace: 'nowrap',
      border: 0,
    },
  },
  tableHeaderCell: {
    paddingTop: tokens.spacingVerticalL,
    paddingRight: tokens.spacingHorizontalL,
    paddingBottom: tokens.spacingVerticalL,
    paddingLeft: tokens.spacingHorizontalL,
  },
  tableBody: {
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
    },
  },
  scenarioColumn: {
    width: '40%',
  },
  sizeColumn: {
    width: '20%',
  },
  techniqueColumn: {
    width: '20%',
  },
  datasetColumn: {
    width: '20%',
  },
  summaryRow: {
    color: tokens.colorNeutralForeground1,
    ':hover': {
      backgroundColor: tokens.colorNeutralBackground1Hover,
    },
    [NARROW_VIEWPORT_QUERY]: {
      display: 'grid',
      gridTemplateRows: 'repeat(4, max-content)',
      height: 'max-content',
      width: '100%',
      marginBottom: tokens.spacingVerticalM,
      overflow: 'hidden',
      border: `1px solid ${tokens.colorNeutralStroke2}`,
      borderRadius: tokens.borderRadiusLarge,
      backgroundColor: tokens.colorNeutralBackground1,
    },
  },
  tableCell: {
    verticalAlign: 'top',
    overflowWrap: 'anywhere',
    [NARROW_VIEWPORT_QUERY]: {
      display: 'grid',
      gridTemplateColumns: 'minmax(7rem, 35%) minmax(0, 1fr)',
      gap: tokens.spacingHorizontalM,
      height: 'auto',
      width: 'auto',
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
      borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
      ':last-child': {
        borderBottom: 0,
      },
    },
  },
  tableCellPadding: {
    paddingTop: tokens.spacingVerticalL,
    paddingRight: tokens.spacingHorizontalL,
    paddingBottom: tokens.spacingVerticalL,
    paddingLeft: tokens.spacingHorizontalL,
  },
  mobileLabel: {
    display: 'none',
    color: tokens.colorNeutralForeground3,
    [NARROW_VIEWPORT_QUERY]: {
      display: 'block',
    },
  },
  scenarioSummary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  scenarioLink: {
    display: 'inline-flex',
    alignItems: 'center',
    alignSelf: 'flex-start',
    color: tokens.colorBrandForegroundLink,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    overflowWrap: 'anywhere',
    ':hover': {
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  purposePreview: {
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase300,
    '& p': {
      marginBottom: tokens.spacingVerticalXS,
    },
    '& ul, & ol': {
      marginBottom: tokens.spacingVerticalXS,
    },
    '& h1, & h2, & h3, & h4, & h5, & h6': {
      margin: `${tokens.spacingVerticalXS} 0`,
      fontSize: tokens.fontSizeBase300,
      lineHeight: tokens.lineHeightBase300,
    },
  },
  purposePreviewCollapsed: {
    maxHeight: '3.75rem',
    overflow: 'hidden',
  },
  inlineSummaryCollapsed: {
    maxHeight: '2.75rem',
    overflow: 'hidden',
  },
  descriptionToggle: {
    alignSelf: 'flex-start',
    minWidth: 0,
    width: '2rem',
    height: '2rem',
    padding: 0,
  },
  compactStack: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
  secondaryText: {
    color: tokens.colorNeutralForeground3,
  },
})
