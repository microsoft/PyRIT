import { makeStyles, tokens } from '@fluentui/react-components'

export const useLaunchPresetDialogStyles = makeStyles({
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  summary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  scenarioLine: {
    color: tokens.colorNeutralForeground3,
  },
  numberInput: {
    maxWidth: '10rem',
  },
})
