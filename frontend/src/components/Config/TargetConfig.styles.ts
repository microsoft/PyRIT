import { makeStyles, tokens } from '@fluentui/react-components'

export const useTargetConfigStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100%',
    width: '100%',
    minWidth: 0,
    maxWidth: '100%',
    padding: tokens.spacingVerticalXXL,
    overflowX: 'hidden',
    overflowY: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
    '@media (max-width: 600px)': {
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}`,
    },
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    minWidth: 0,
    gap: tokens.spacingVerticalM,
    marginBottom: tokens.spacingVerticalXL,
    '@media (max-width: 600px)': {
      flexDirection: 'column',
      alignItems: 'stretch',
    },
  },
  headerLeft: {
    display: 'flex',
    flexDirection: 'column',
    minWidth: 0,
    gap: tokens.spacingVerticalXS,
  },
  headerActions: {
    display: 'flex',
    flexWrap: 'wrap',
    minWidth: 0,
    gap: tokens.spacingHorizontalS,
    '@media (max-width: 600px)': {
      width: '100%',
    },
  },
  headerAction: {
    '@media (max-width: 600px)': {
      flex: '1 1 8rem',
      minHeight: '44px',
    },
  },
  emptyState: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    padding: tokens.spacingVerticalXXXL,
    gap: tokens.spacingVerticalM,
  },
  loadingState: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: tokens.spacingVerticalXXXL,
  },
  errorState: {
    padding: tokens.spacingVerticalL,
    color: tokens.colorPaletteRedForeground1,
    textAlign: 'center',
  },
})
