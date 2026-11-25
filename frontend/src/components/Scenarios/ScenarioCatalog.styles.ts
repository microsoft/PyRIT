import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, NARROW_VIEWPORT_QUERY, TOUCH_INPUT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

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
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
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
  messages: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    marginBottom: tokens.spacingVerticalL,
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
})
