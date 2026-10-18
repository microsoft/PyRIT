import { makeStyles, tokens } from '@fluentui/react-components'

import { NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useAttackAttemptDetailsStyles = makeStyles({
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    overflowWrap: 'anywhere',
  },
  summary: {
    display: 'grid',
    gridTemplateColumns: 'repeat(5, minmax(8rem, 1fr))',
    gap: `${tokens.spacingVerticalM} ${tokens.spacingHorizontalL}`,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
    },
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXS,
  },
  sectionLabel: {
    color: tokens.colorNeutralForeground3,
  },
  surface: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    padding: tokens.spacingVerticalL,
    borderLeft: `3px solid ${tokens.colorBrandStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    backgroundColor: tokens.colorNeutralBackground2,
  },
  bodyText: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
  },
  collapsedSeedText: {
    display: '-webkit-box',
    overflow: 'hidden',
    WebkitBoxOrient: 'vertical',
    WebkitLineClamp: 5,
  },
  badgeList: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  seed: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  seedImage: {
    display: 'block',
    width: 'auto',
    maxWidth: '100%',
    maxHeight: '24rem',
    borderRadius: tokens.borderRadiusMedium,
    objectFit: 'contain',
  },
  seedMedia: {
    width: 'min(36rem, 100%)',
  },
  seedToggle: {
    alignSelf: 'flex-start',
    minWidth: 0,
    paddingInline: 0,
    color: tokens.colorBrandForegroundLink,
  },
  fileLink: {
    color: tokens.colorBrandForegroundLink,
  },
  conversationLink: {
    alignSelf: 'flex-start',
    color: tokens.colorBrandForegroundLink,
  },
})
