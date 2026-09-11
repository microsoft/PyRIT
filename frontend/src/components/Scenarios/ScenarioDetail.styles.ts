import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  mobileTouchTarget,
  mobileTouchTargetHeight,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useScenarioDetailStyles = makeStyles({
  root: {
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
  content: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    maxWidth: '80rem',
    minWidth: 0,
    margin: '0 auto',
    gap: tokens.spacingVerticalL,
  },
  backLink: {
    alignSelf: 'flex-start',
  },
  headerText: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  description: {
    color: tokens.colorNeutralForeground3,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
  },
  layout: {
    display: 'block',
    minWidth: 0,
  },
  formColumn: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    minWidth: 0,
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  control: {
    ...mobileTouchTargetHeight,
    '& > select': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    '& > input': {
      [TOUCH_INPUT_QUERY]: {
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  techniqueList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  selectionControl: {
    ...mobileTouchTargetHeight,
  },
  techniqueOption: {
    display: 'grid',
    gridTemplateColumns: 'minmax(12rem, 35%) minmax(0, 1fr)',
    gap: tokens.spacingHorizontalM,
    alignItems: 'start',
    padding: tokens.spacingVerticalS,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusMedium,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
      gap: tokens.spacingVerticalXS,
    },
  },
  techniqueDetails: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
  techniqueTags: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  techniqueTag: {
    ...mobileTouchTarget,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  dynamicParameters: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
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
    minHeight: '20rem',
    padding: tokens.spacingVerticalXXXL,
    textAlign: 'center',
    color: tokens.colorNeutralForeground3,
  },
  numberInput: {
    maxWidth: '10rem',
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  launchSection: {
    display: 'flex',
    minWidth: 0,
    padding: 0,
    borderRadius: tokens.borderRadiusLarge,
    overflow: 'hidden',
  },
  estimateHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
  },
  costEstimateList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
    margin: 0,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  costEstimateRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto',
    gap: tokens.spacingHorizontalL,
    padding: `${tokens.spacingVerticalS} 0`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    '& > dt': {
      color: tokens.colorNeutralForeground2,
    },
    '& > dd': {
      margin: 0,
      fontWeight: tokens.fontWeightSemibold,
      textAlign: 'right',
      overflowWrap: 'anywhere',
    },
  },
  totalEstimateRow: {
    padding: `${tokens.spacingVerticalM} 0`,
    '& > dt': {
      color: tokens.colorNeutralForeground1,
      fontWeight: tokens.fontWeightRegular,
    },
    '& > dd': {
      fontWeight: tokens.fontWeightRegular,
    },
  },
  inlineStatus: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  warningText: {
    color: tokens.colorPaletteDarkOrangeForeground1,
  },
  dialogContent: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    maxHeight: '65vh',
    overflowY: 'auto',
  },
  previewList: {
    display: 'flex',
    flexDirection: 'column',
    gap: 0,
    margin: 0,
  },
  previewGroup: {
    display: 'grid',
    gridTemplateColumns: 'minmax(7rem, 38%) minmax(0, 1fr)',
    gap: tokens.spacingHorizontalM,
    padding: `${tokens.spacingVerticalM} 0`,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    '& > dt': {
      color: tokens.colorNeutralForeground3,
      fontWeight: tokens.fontWeightSemibold,
    },
    '& > dd': {
      minWidth: 0,
      margin: 0,
      overflowWrap: 'anywhere',
    },
  },
  previewStack: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  previewBadges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXS,
  },
  parameterPreview: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    margin: 0,
  },
  parameterPreviewRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto',
    gap: tokens.spacingHorizontalS,
    '& > dt': {
      overflowWrap: 'anywhere',
    },
    '& > dd': {
      margin: 0,
      fontWeight: tokens.fontWeightSemibold,
      overflowWrap: 'anywhere',
    },
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
  },
  launchButton: {
    width: '100%',
    minHeight: '3.5rem',
    borderRadius: tokens.borderRadiusLarge,
  },
})
