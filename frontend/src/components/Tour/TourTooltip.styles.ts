import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget } from '../../styles/touchTargets'

export const useTourTooltipStyles = makeStyles({
  // Include the mascot's overhang in the floating bounds so Joyride can keep it in the viewport.
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    width: '420px',
    maxWidth: `calc(100vw - ${tokens.spacingHorizontalM} - ${tokens.spacingHorizontalM})`,
    paddingBottom: `calc(${tokens.spacingVerticalXXL} + ${tokens.spacingVerticalL})`,
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
    bottom: 0,
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
    ...mobileTouchTarget,
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
    ...mobileTouchTarget,
  },
})
