import { makeStyles, tokens } from '@fluentui/react-components'

export const useTourTooltipStyles = makeStyles({
  // Outer wrapper: provides space for the mascot to overflow below the card
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    maxWidth: '420px',
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
  },
  content: {
    color: tokens.colorNeutralForeground1,
    lineHeight: tokens.lineHeightBase300,
  },
  footer: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalS,
  },
  stepCounter: {
    color: tokens.colorNeutralForeground3,
    whiteSpace: 'nowrap',
  },
  actions: {
    display: 'flex',
    gap: tokens.spacingHorizontalS,
    marginLeft: 'auto',
  },
})
