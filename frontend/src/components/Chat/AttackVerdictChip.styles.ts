import { makeStyles, tokens } from '@fluentui/react-components'

export const useAttackVerdictChipStyles = makeStyles({
  chip: {
    display: 'inline-flex',
    alignItems: 'center',
    columnGap: tokens.spacingHorizontalXS,
  },
  chipLabel: {
    textTransform: 'capitalize',
  },
  surface: {
    display: 'flex',
    flexDirection: 'column',
    rowGap: tokens.spacingVerticalXS,
    minWidth: '240px',
    maxWidth: '360px',
  },
  row: {
    display: 'flex',
    columnGap: tokens.spacingHorizontalS,
  },
  rowLabel: {
    minWidth: '72px',
    color: tokens.colorNeutralForeground2,
  },
  rationaleBlock: {
    display: 'flex',
    flexDirection: 'column',
    rowGap: tokens.spacingVerticalXXS,
    marginTop: tokens.spacingVerticalXS,
    paddingTop: tokens.spacingVerticalXS,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  rationaleText: {
    color: tokens.colorNeutralForeground2,
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    maxHeight: '30vh',
    overflowY: 'auto',
  },
})
