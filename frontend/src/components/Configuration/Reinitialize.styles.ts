import { makeStyles, tokens } from '@fluentui/react-components'

export const useReinitializeStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalS,
  },
})
