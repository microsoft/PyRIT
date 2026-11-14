import { makeStyles, tokens } from '@fluentui/react-components'

export const useScoreDetailsStyles = makeStyles({
  surface: {
    display: 'flex',
    flexDirection: 'column',
    rowGap: tokens.spacingVerticalXS,
    width: 'min(360px, calc(100vw - 64px))',
    minWidth: 0,
    maxWidth: '100%',
  },
  row: {
    display: 'flex',
    alignItems: 'flex-start',
    columnGap: tokens.spacingHorizontalS,
    width: '100%',
    minWidth: 0,
  },
  label: {
    flex: '0 0 72px',
    color: tokens.colorNeutralForeground2,
  },
  value: {
    display: 'block',
    flex: '0 1 auto',
    minWidth: 0,
    maxWidth: '100%',
    height: 'auto',
    whiteSpace: 'normal',
    overflowWrap: 'anywhere',
    wordBreak: 'break-word',
  },
  rationale: {
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
