import { makeStyles, tokens } from '@fluentui/react-components'

export const useAssertPolicyMetricsPanelStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  hint: {
    color: tokens.colorNeutralForeground3,
  },
  tableScroll: {
    overflowX: 'auto',
  },
  label: {
    fontWeight: tokens.fontWeightSemibold,
  },
  rateCell: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    paddingTop: tokens.spacingVerticalXS,
    paddingBottom: tokens.spacingVerticalXS,
  },
})
