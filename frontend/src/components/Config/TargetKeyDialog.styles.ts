import { makeStyles, tokens } from '@fluentui/react-components'

export const useTargetKeyDialogStyles = makeStyles({
  content: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    gap: tokens.spacingVerticalM,
  },
  targetName: {
    overflowWrap: 'anywhere',
  },
})
