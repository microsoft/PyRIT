import { makeStyles, tokens } from '@fluentui/react-components'

export const useTourTooltipStyles = makeStyles({
  // Outer wrapper: provides space for the mascot to overflow below the card
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    width: '420px',
    maxWidth: `calc(100vw - ${tokens.spacingHorizontalM} - ${tokens.spacingHorizontalM})`,
    position: 'relative',
  },
  container: {
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusLarge,
    boxShadow: tokens.shadow16,
    padding: tokens.spacingHorizontalL,
    // Leave space at bottom-left for the mascot to overlap
    paddingBottom: tokens.spacingVerticalXXL,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  // Mascot positioned at bottom-left, overlapping the card edge
  mascot: {
    position: 'absolute',
    bottom: '-40px',
    left: '-20px',
    width: '90px',
    height: '90px',
    objectFit: 'contain',
    pointerEvents: 'none',
    zIndex: 1,
    '@media (max-width: 600px)': {
      left: 0,
    },
  },
  closeRow: {
    display: 'flex',
    justifyContent: 'flex-end',
    marginBottom: '-8px',
    marginTop: '-4px',
  },
  closeButton: {
    '@media (max-width: 600px)': {
      minWidth: '44px',
      minHeight: '44px',
    },
  },
  content: {
    color: tokens.colorNeutralForeground1,
    lineHeight: tokens.lineHeightBase300,
    overflowWrap: 'anywhere',
  },
  footer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
    flexWrap: 'wrap',
    paddingLeft: '72px',
  },
  stepCounter: {
    color: tokens.colorNeutralForeground3,
    whiteSpace: 'nowrap',
  },
  actions: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
    marginLeft: 'auto',
    flexWrap: 'wrap',
    justifyContent: 'flex-end',
  },
  actionButton: {
    '@media (max-width: 600px)': {
      minWidth: '44px',
      minHeight: '44px',
    },
  },
})
