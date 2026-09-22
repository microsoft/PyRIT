import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useMainLayoutStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    width: '100vw',
    overflow: 'hidden',
  },
  skipLink: {
    position: 'absolute',
    top: '0',
    left: '0',
    zIndex: 1000,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalM}`,
    backgroundColor: tokens.colorBrandBackground,
    color: tokens.colorNeutralForegroundOnBrand,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    borderBottomRightRadius: tokens.borderRadiusMedium,
    // translateY(-100%) hides the link above the viewport regardless of its
    // own rendered height (text zoom, a different font, or longer copy can
    // all change that height), unlike a fixed 'top' offset.
    transform: 'translateY(-100%)',
    transitionProperty: 'transform',
    transitionDuration: tokens.durationFast,
    '@media (prefers-reduced-motion: reduce)': {
      transitionDuration: '0s',
    },
    ':focus-visible': {
      transform: 'translateY(0)',
    },
  },
  topBar: {
    minHeight: '60px',
    flexShrink: 0,
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    display: 'flex',
    alignItems: 'center',
    padding: `0 ${tokens.spacingHorizontalL}`,
    gap: tokens.spacingHorizontalM,
    [NARROW_VIEWPORT_QUERY]: {
      flexWrap: 'wrap',
    },
  },
  logo: {
    width: '40px',
    height: '40px',
    flexShrink: 0,
    cursor: 'help',
  },
  title: {
    fontSize: tokens.fontSizeHero700,
    lineHeight: tokens.lineHeightHero700,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorBrandForeground1,
    minWidth: 0,
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    [NARROW_VIEWPORT_QUERY]: {
      flex: '1 1 0%',
      minWidth: '8ch',
      fontSize: tokens.fontSizeBase500,
      lineHeight: tokens.lineHeightBase500,
    },
  },
  subtitle: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    marginLeft: tokens.spacingHorizontalXS,
    [NARROW_VIEWPORT_QUERY]: {
      display: 'none',
    },
  },
  spacer: {
    flex: 1,
    [NARROW_VIEWPORT_QUERY]: {
      display: 'none',
    },
  },
  tourButton: {
    flexShrink: 0,
    whiteSpace: 'nowrap',
    [NARROW_VIEWPORT_QUERY]: {
      minWidth: '32px',
      paddingInline: 0,
    },
    ...mobileTouchTarget,
  },
  tourLabel: {
    [NARROW_VIEWPORT_QUERY]: {
      display: 'none',
    },
  },
  contentArea: {
    display: 'flex',
    flex: 1,
    minWidth: 0,
    overflow: 'hidden',
  },
  sidebar: {
    width: '60px',
    flexShrink: 0,
    backgroundColor: tokens.colorNeutralBackground3,
    borderRight: `1px solid ${tokens.colorNeutralStroke1}`,
    display: 'flex',
    flexDirection: 'column',
  },
  main: {
    position: 'relative',
    isolation: 'isolate',
    flex: 1,
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
  labelsSection: {
    flexShrink: 0,
    minWidth: 0,
    padding: `${tokens.spacingVerticalS} ${tokens.spacingHorizontalL}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  labelsRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
    minWidth: 0,
  },
  labelsControls: {
    display: 'flex',
    alignItems: 'center',
    flex: '1 1 260px',
    gap: tokens.spacingHorizontalM,
    minWidth: 0,
    [NARROW_VIEWPORT_QUERY]: {
      flexWrap: 'wrap',
    },
  },
  labelsTitle: {
    flexShrink: 0,
    [NARROW_VIEWPORT_QUERY]: {
      flexBasis: '100%',
    },
  },
  labelsHint: {
    color: tokens.colorNeutralForeground2,
  },
  toolbarSlot: {
    maxWidth: '100%',
    marginLeft: 'auto',
    ':empty': {
      display: 'none',
    },
  },
  decorated: {
    backgroundColor: tokens.colorNeutralBackground2,
  },
  background: {
    position: 'absolute',
    inset: 0,
    zIndex: -1,
    pointerEvents: 'none',
    backgroundSize: 'cover',
    backgroundPosition: 'right bottom',
    backgroundRepeat: 'no-repeat',
    '@media (forced-colors: active)': {
      display: 'none',
    },
  },
})
