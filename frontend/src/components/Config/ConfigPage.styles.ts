import { makeStyles, tokens } from '@fluentui/react-components'

export const useConfigPageStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    minWidth: 0,
    maxWidth: '100%',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  tabBar: {
    flexShrink: 0,
    paddingLeft: tokens.spacingHorizontalXXL,
    paddingRight: tokens.spacingHorizontalXXL,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
  tabPanel: {
    flex: '1 1 auto',
    minHeight: 0,
    overflow: 'auto',
  },
})
