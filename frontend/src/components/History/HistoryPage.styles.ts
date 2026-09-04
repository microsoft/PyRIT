import { makeStyles, tokens } from '@fluentui/react-components'

export const useHistoryPageStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    overflow: 'hidden',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXL,
    padding: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalXXL} 0`,
    backgroundColor: tokens.colorNeutralBackground3,
  },
  content: {
    flex: 1,
    minHeight: 0,
    overflow: 'hidden',
  },
})
