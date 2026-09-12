import { makeStyles, tokens } from '@fluentui/react-components'

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
    height: '60px',
    backgroundColor: tokens.colorNeutralBackground3,
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    display: 'flex',
    alignItems: 'center',
    padding: `0 ${tokens.spacingHorizontalL}`,
    gap: tokens.spacingHorizontalM,
  },
  logo: {
    width: '40px',
    height: '40px',
    cursor: 'help',
  },
  title: {
    fontSize: tokens.fontSizeHero700,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorBrandForeground1,
  },
  subtitle: {
    fontSize: tokens.fontSizeBase200,
    color: tokens.colorNeutralForeground3,
    marginLeft: tokens.spacingHorizontalXS,
  },
  spacer: {
    flex: 1,
  },
  tourButton: {
    '@media (max-width: 600px)': {
      minHeight: '44px',
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
    flex: 1,
    minWidth: 0,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
  },
})
