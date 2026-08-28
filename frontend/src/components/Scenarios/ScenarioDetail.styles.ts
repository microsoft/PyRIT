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
    maxWidth: '75ch',
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase400,
    lineHeight: tokens.lineHeightBase500,
  },
  layout: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(18rem, 23rem)',
    alignItems: 'start',
    gap: tokens.spacingHorizontalXXL,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
      gap: tokens.spacingVerticalXL,
    },
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
  advancedSection: {
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  advancedFields: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    paddingTop: tokens.spacingVerticalS,
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
  previewRail: {
    position: 'sticky',
    top: 0,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    minWidth: 0,
    padding: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusLarge,
    backgroundColor: tokens.colorNeutralBackground1,
    [NARROW_VIEWPORT_QUERY]: {
      position: 'static',
    },
  },
  previewHeader: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
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
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(7rem, 35%) minmax(0, 1fr)',
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
  errorText: {
    color: tokens.colorPaletteRedForeground1,
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
  estimateGroup: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    paddingTop: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  previewActions: {
    paddingTop: tokens.spacingVerticalM,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  launchButton: {
    width: '100%',
    ...mobileTouchTargetHeight,
  },
})
