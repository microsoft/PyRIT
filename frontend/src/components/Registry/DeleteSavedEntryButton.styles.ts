import { makeStyles, tokens } from '@fluentui/react-components'

export const useDeleteSavedEntryStyles = makeStyles({
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    overflowWrap: 'anywhere',
  },
})
