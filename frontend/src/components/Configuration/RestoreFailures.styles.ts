import { makeStyles, tokens } from '@fluentui/react-components'

export const useRestoreFailuresStyles = makeStyles({
  // The page doesn't scroll past the editor, so a long list scrolls on its own.
  list: {
    maxHeight: '30vh',
    overflowY: 'auto',
  },
  delete: {
    marginLeft: tokens.spacingHorizontalS,
  },
})
