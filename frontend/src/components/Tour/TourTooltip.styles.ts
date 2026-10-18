import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget } from '../../styles/touchTargets'

export const useTourTooltipStyles = makeStyles({
  // Carries the card surface so the mascot's strip sits inside a single border
  // rather than below a second one.
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    width: '420px',
    maxWidth: `calc(100vw - ${tokens.spacingHorizontalM} - ${tokens.spacingHorizontalM})`,
    // Reserves the mascot's height below the footer.
    paddingBottom: `calc(${tokens.spacingVerticalXXL} + ${tokens.spacingVerticalL})`,
    position: 'relative',
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusLarge,
    boxShadow: tokens.shadow16,
  },
  container: {
    padding: tokens.spacingHorizontalL,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  // Mascot rests on the card's bottom edge, overlapping the footer's left gutter
  mascot: {
    position: 'absolute',
    bottom: 0,
    left: tokens.spacingHorizontalS,
    width: '90px',
    height: '90px',
    objectFit: 'contain',
    pointerEvents: 'none',
    zIndex: 1,
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
