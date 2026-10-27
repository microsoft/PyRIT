import { makeStyles, tokens } from '@fluentui/react-components'

export const useScannerSuiteSectionStyles = makeStyles({
  suiteColumn: {
    width: '60%',
  },
  scenariosColumn: {
    width: '25%',
  },
  actionsColumn: {
    width: '15%',
  },
  titleRow: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalS,
  },
  memberSummary: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
  },
  // No `display` here: any value would override the `hidden` attribute that collapses the list.
  memberList: {
    margin: 0,
    paddingLeft: tokens.spacingHorizontalXL,
  },
})
