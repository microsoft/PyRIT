import { makeStyles, tokens } from '@fluentui/react-components'

import {
  MINIMUM_TOUCH_TARGET_SIZE,
  NARROW_VIEWPORT_QUERY,
  TOUCH_INPUT_QUERY,
} from '@/styles/touchTargets'

export const useConfigurationStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    boxSizing: 'border-box',
    height: '100%',
    width: '100%',
    minWidth: 0,
    maxWidth: '100%',
    gap: tokens.spacingVerticalL,
    padding: tokens.spacingVerticalXXL,
    overflow: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
    '@media (max-width: 600px)': {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingVerticalM,
  },
  actions: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
    [NARROW_VIEWPORT_QUERY]: {
      width: '100%',
    },
  },
  action: {
    [NARROW_VIEWPORT_QUERY]: {
      flex: '1 1 8rem',
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
    [TOUCH_INPUT_QUERY]: {
      minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    },
  },
  editorField: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    maxWidth: '100%',
  },
  editor: {
    position: 'relative',
    flex: 1,
    minHeight: '24rem',
  },
  editorHighlight: {
    position: 'absolute',
    inset: 0,
    boxSizing: 'border-box',
    minHeight: '24rem',
    margin: 0,
    padding: tokens.spacingHorizontalL,
    overflow: 'hidden',
    pointerEvents: 'none',
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: `0 0 ${tokens.borderRadiusMedium} ${tokens.borderRadiusMedium}`,
    backgroundColor: tokens.colorNeutralBackground1,
    color: tokens.colorNeutralForeground1,
    whiteSpace: 'pre',
    tabSize: 2,
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase300,
    '& .token.comment': {
      color: tokens.colorNeutralForeground3,
      fontStyle: 'italic',
    },
    '& .token.key, & .token.atrule, & .token.tag': {
      color: tokens.colorPaletteBlueForeground2,
    },
    '& .token.string, & .token.scalar': {
      color: tokens.colorPaletteGreenForeground1,
    },
    '& .token.number, & .token.boolean, & .token.null, & .token.important': {
      color: tokens.colorPaletteDarkOrangeForeground1,
    },
    '& .token.anchor, & .token.alias': {
      color: tokens.colorPalettePurpleForeground2,
    },
    '& .token.punctuation': {
      color: tokens.colorNeutralForeground2,
    },
  },
  editorInput: {
    position: 'relative',
    zIndex: 1,
    display: 'block',
    boxSizing: 'border-box',
    width: '100%',
    height: '100%',
    minHeight: '24rem',
    margin: 0,
    padding: tokens.spacingHorizontalL,
    overflow: 'auto',
    resize: 'vertical',
    border: '1px solid transparent',
    borderRadius: `0 0 ${tokens.borderRadiusMedium} ${tokens.borderRadiusMedium}`,
    backgroundColor: 'transparent',
    color: 'transparent',
    caretColor: tokens.colorNeutralForeground1,
    whiteSpace: 'pre',
    tabSize: 2,
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase300,
    '&:focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '-2px',
    },
    '&::selection': {
      backgroundColor: tokens.colorBrandBackground2,
    },
  },
  loadingState: {
    display: 'flex',
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: '16rem',
  },
  message: {
    width: '100%',
  },
  environmentSection: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
    maxWidth: '100%',
    gap: tokens.spacingVerticalM,
  },
})
