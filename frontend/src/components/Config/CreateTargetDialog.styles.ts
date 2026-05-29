import { makeStyles, tokens } from '@fluentui/react-components'

export const useCreateTargetDialogStyles = makeStyles({
  form: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
  },
  warningMessage: {
    width: '100%',
  },
  warningMessageBody: {
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
})
