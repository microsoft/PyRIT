import { makeStyles, tokens } from '@fluentui/react-components'

export const useCustomInitializersStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    gap: tokens.spacingVerticalM,
    minWidth: 0,
    minHeight: 0,
  },
  editorField: {
    display: 'flex',
    flexDirection: 'column',
    flex: 1,
    minWidth: 0,
    minHeight: 0,
  },
  editorActions: {
    display: 'flex',
    justifyContent: 'flex-end',
    gap: tokens.spacingHorizontalS,
  },
  dialogBody: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  sourceDialog: {
    width: 'min(60rem, 90vw)',
    maxWidth: 'none',
  },
})
