import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget } from '../../styles/touchTargets'

export const useAttackOrchestrationViewStyles = makeStyles({
  root: {
    display: 'flex',
    flexDirection: 'column',
    width: '100%',
    height: '100%',
    minWidth: 0,
    overflow: 'hidden',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  breadcrumbBar: {
    display: 'flex',
    alignItems: 'center',
    flexShrink: 0,
    minHeight: '36px',
    paddingInline: tokens.spacingHorizontalL,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    backgroundColor: tokens.colorNeutralBackground3,
    overflowX: 'auto',
  },
  breadcrumbLink: {
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    whiteSpace: 'nowrap',
    ':hover': {
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  scrollArea: {
    flex: 1,
    minWidth: 0,
    overflowY: 'auto',
  },
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXL,
    width: 'min(960px, 100%)',
    marginInline: 'auto',
    padding: `${tokens.spacingVerticalXXL} ${tokens.spacingHorizontalXXL}`,
    boxSizing: 'border-box',
    '@media (max-width: 600px)': {
      gap: tokens.spacingVerticalXL,
      padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalL}`,
    },
  },
  summary: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  titleRow: {
    display: 'flex',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: tokens.spacingHorizontalL,
    '@media (max-width: 600px)': {
      flexDirection: 'column',
      gap: tokens.spacingVerticalS,
    },
  },
  title: {
    margin: 0,
    color: tokens.colorNeutralForeground1,
    fontSize: tokens.fontSizeHero800,
    lineHeight: tokens.lineHeightHero800,
    fontWeight: tokens.fontWeightSemibold,
    letterSpacing: '-0.02em',
    overflowWrap: 'anywhere',
    '@media (max-width: 600px)': {
      fontSize: tokens.fontSizeHero700,
      lineHeight: tokens.lineHeightHero700,
    },
  },
  description: {
    maxWidth: '72ch',
    color: tokens.colorNeutralForeground2,
    lineHeight: tokens.lineHeightBase400,
  },
  facts: {
    display: 'grid',
    gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
    gap: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalXXL}`,
    margin: 0,
    paddingBlock: tokens.spacingVerticalL,
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    '@media (max-width: 760px)': {
      gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
    },
    '@media (max-width: 480px)': {
      gridTemplateColumns: '1fr',
    },
  },
  fact: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
    '& dt': {
      color: tokens.colorNeutralForeground3,
      fontSize: tokens.fontSizeBase200,
    },
    '& dd': {
      margin: 0,
      color: tokens.colorNeutralForeground1,
      fontSize: tokens.fontSizeBase300,
      fontWeight: tokens.fontWeightSemibold,
      overflowWrap: 'anywhere',
    },
  },
  objectiveFact: {
    gridColumn: '1 / -1',
    '& dd': {
      fontWeight: tokens.fontWeightRegular,
      maxWidth: '72ch',
    },
  },
  attemptsSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  sectionHeading: {
    margin: 0,
    color: tokens.colorNeutralForeground1,
    fontSize: tokens.fontSizeBase500,
    lineHeight: tokens.lineHeightBase500,
    fontWeight: tokens.fontWeightSemibold,
  },
  sectionDescription: {
    maxWidth: '72ch',
    color: tokens.colorNeutralForeground2,
  },
  loading: {
    display: 'flex',
    justifyContent: 'flex-start',
    paddingBlock: tokens.spacingVerticalXL,
  },
  attemptList: {
    display: 'flex',
    flexDirection: 'column',
    margin: 0,
    padding: 0,
    listStyleType: 'none',
    borderTop: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  attemptRow: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) auto',
    alignItems: 'center',
    gap: tokens.spacingHorizontalL,
    paddingBlock: tokens.spacingVerticalL,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
    '@media (max-width: 600px)': {
      gridTemplateColumns: '1fr',
      gap: tokens.spacingVerticalM,
    },
  },
  attemptInfo: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
    minWidth: 0,
  },
  attemptTitleRow: {
    display: 'flex',
    alignItems: 'center',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalS,
  },
  attemptName: {
    color: tokens.colorNeutralForeground1,
    fontWeight: tokens.fontWeightSemibold,
    overflowWrap: 'anywhere',
  },
  attemptMeta: {
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    overflowWrap: 'anywhere',
  },
  childLink: {
    ...mobileTouchTarget,
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    color: tokens.colorBrandForegroundLink,
    fontWeight: tokens.fontWeightSemibold,
    textDecorationLine: 'none',
    paddingInline: tokens.spacingHorizontalM,
    borderRadius: tokens.borderRadiusMedium,
    whiteSpace: 'nowrap',
    ':hover': {
      color: tokens.colorBrandForegroundLinkHover,
      backgroundColor: tokens.colorSubtleBackgroundHover,
      textDecorationLine: 'underline',
    },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
    '@media (max-width: 600px)': {
      justifySelf: 'stretch',
    },
  },
})
