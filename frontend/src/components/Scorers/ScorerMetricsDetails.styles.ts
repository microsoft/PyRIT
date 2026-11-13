import { makeStyles, tokens } from '@fluentui/react-components'

export const useScorerMetricsDetailsStyles = makeStyles({
  metric: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  label: {
    color: tokens.colorNeutralForeground3,
  },
  value: {
    overflowWrap: 'anywhere',
    whiteSpace: 'pre-wrap',
  },
  metrics: {
    display: 'grid',
    gap: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))',
  },
})
