import { makeStyles, tokens } from '@fluentui/react-components'

export const useComponentIdentityDetailsStyles = makeStyles({
  identity: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    minWidth: 0,
  },
  fields: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(14rem, 1fr))',
    gap: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
  },
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
  childSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  childList: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  child: {
    padding: tokens.spacingVerticalM,
    borderLeft: `3px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground1,
  },
})
