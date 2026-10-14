import { makeStyles, tokens } from '@fluentui/react-components'

import {
  mobileTouchTarget,
  mobileTouchTargetHeight,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
  MINIMUM_TOUCH_TARGET_SIZE,
} from '@/styles/touchTargets'

export const useSeedExampleBrowserStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    width: '100%',
    minWidth: 0,
  },
  filters: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
    alignItems: 'center',
    [NARROW_VIEWPORT_QUERY]: {
      alignItems: 'stretch',
    },
  },
  search: {
    minWidth: '16rem',
    maxWidth: '100%',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
      flex: '1 1 100%',
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
    '& input': {
      [TOUCH_INPUT_QUERY]: {
        height: MINIMUM_TOUCH_TARGET_SIZE,
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
  },
  filterControl: {
    minWidth: '12rem',
    maxWidth: '100%',
    ...mobileTouchTargetHeight,
    '& > input, & select': {
      [TOUCH_INPUT_QUERY]: {
        height: MINIMUM_TOUCH_TARGET_SIZE,
        minHeight: MINIMUM_TOUCH_TARGET_SIZE,
      },
    },
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: 0,
      flex: '1 1 12rem',
    },
  },
  touchTarget: {
    ...mobileTouchTarget,
    maxWidth: '100%',
  },
  help: {
    margin: 0,
    maxWidth: '70ch',
    color: tokens.colorNeutralForeground3,
  },
  status: {
    margin: 0,
    color: tokens.colorNeutralForeground2,
  },
  tableWrap: {
    width: '100%',
    minWidth: 0,
    overflowX: 'auto',
  },
  table: {
    width: '100%',
    minWidth: '64rem',
    tableLayout: 'fixed',
  },
  cell: {
    minWidth: 0,
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
    verticalAlign: 'top',
  },
  preview: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
  badges: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
    minWidth: 0,
    maxWidth: '100%',
  },
  valueBadge: {
    '&&': {
      maxWidth: '100%',
      minWidth: 0,
      height: 'auto',
      minHeight: '20px',
      whiteSpace: 'normal',
      overflowWrap: 'anywhere',
      wordBreak: 'break-word',
      textAlign: 'start',
    },
  },
  showButton: {
    '&&': {
      maxWidth: '100%',
      height: 'auto',
      whiteSpace: 'normal',
      ...mobileTouchTargetHeight,
    },
  },
  pagination: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
  },
  message: {
    margin: 0,
    maxWidth: '70ch',
    ':focus': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
})
