import { makeStyles, tokens } from '@fluentui/react-components'

export const useRestoreFailuresStyles = makeStyles({
  // The page doesn't scroll past the editor, so a long list scrolls on its own. It fills its column, and a long
  // name or message wraps instead of widening the list past it.
  list: {
    alignSelf: 'stretch',
    boxSizing: 'border-box',
    maxWidth: '100%',
    minWidth: 0,
    maxHeight: '30vh',
    overflowWrap: 'anywhere',
    overflowY: 'auto',
  },
  delete: {
    marginLeft: tokens.spacingHorizontalS,
  },
})
