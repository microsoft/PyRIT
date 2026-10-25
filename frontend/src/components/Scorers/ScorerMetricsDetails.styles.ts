import { makeStyles, tokens } from '@fluentui/react-components'

export const useScorerMetricsDetailsStyles = makeStyles({
  metrics: {
    display: 'grid',
    gap: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    gridTemplateColumns: 'repeat(auto-fit, minmax(9rem, 1fr))',
  },
})
