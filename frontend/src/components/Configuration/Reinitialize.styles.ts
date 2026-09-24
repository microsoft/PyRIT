import { makeStyles, tokens } from '@fluentui/react-components'

export const useReinitializeStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: tokens.spacingVerticalS,
  },
  workTable: {
    marginTop: tokens.spacingVerticalM,
    marginBottom: tokens.spacingVerticalM,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    overflow: 'hidden',
    '& thead': {
      backgroundColor: tokens.colorNeutralBackground3,
    },
    '& tr:not(:last-child)': {
      borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    },
  },
  workLabel: {
    width: '10rem',
    fontWeight: tokens.fontWeightSemibold,
  },
  scenarioList: {
    overflowWrap: 'anywhere',
  },
})
