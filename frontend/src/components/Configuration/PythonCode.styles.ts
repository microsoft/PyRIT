import { makeStyles, tokens } from '@fluentui/react-components'

export const usePythonCodeStyles = makeStyles({
  codeBlock: {
    maxHeight: '65vh',
    margin: 0,
    padding: tokens.spacingHorizontalL,
    overflow: 'auto',
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: `0 0 ${tokens.borderRadiusMedium} ${tokens.borderRadiusMedium}`,
    backgroundColor: tokens.colorNeutralBackground2,
    color: tokens.colorNeutralForeground1,
    whiteSpace: 'pre',
    tabSize: 4,
    fontFamily: 'Consolas, "Courier New", monospace',
    fontSize: tokens.fontSizeBase300,
    lineHeight: tokens.lineHeightBase300,
    '& .token.comment, & .token.prolog, & .token.doctype, & .token.cdata': {
      color: tokens.colorNeutralForeground3,
      fontStyle: 'italic',
    },
    '& .token.keyword, & .token.operator': {
      color: tokens.colorPalettePurpleForeground2,
    },
    '& .token.string, & .token.char, & .token.attr-value': {
      color: tokens.colorPaletteGreenForeground1,
    },
    '& .token.number, & .token.boolean, & .token.constant': {
      color: tokens.colorPaletteDarkOrangeForeground1,
    },
    '& .token.function, & .token.class-name, & .token.builtin': {
      color: tokens.colorPaletteBlueForeground2,
    },
    '& .token.decorator, & .token.annotation, & .token.symbol': {
      color: tokens.colorPaletteRedForeground2,
    },
    '& .token.punctuation': {
      color: tokens.colorNeutralForeground2,
    },
  },
  editor: {
    position: 'relative',
    flex: 1,
    minHeight: '22rem',
  },
  editorHighlight: {
    position: 'absolute',
    inset: 0,
    maxHeight: 'none',
    overflow: 'hidden',
    pointerEvents: 'none',
  },
  editorInput: {
    position: 'relative',
    zIndex: 1,
    display: 'block',
    boxSizing: 'border-box',
    width: '100%',
    height: '100%',
    minHeight: '22rem',
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
    tabSize: 4,
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
})
