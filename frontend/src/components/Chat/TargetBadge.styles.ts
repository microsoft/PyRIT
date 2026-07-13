import { makeStyles, tokens } from '@fluentui/react-components'

export const useTargetBadgeStyles = makeStyles({
  badge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    padding: `2px ${tokens.spacingHorizontalS}`,
    borderRadius: tokens.borderRadiusMedium,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground1,
    cursor: 'help',
    minWidth: 0,
    maxWidth: '100%',
  },
  badgeText: {
    overflow: 'hidden',
    textOverflow: 'ellipsis',
    whiteSpace: 'nowrap',
    minWidth: 0,
  },
  // Applied to the Fluent Tooltip's `content` slot (the actual surface
  // that renders the white/dark popover with the arrow). Fluent caps
  // surface max-width at 240px by default, which truncates anything
  // wider than a short label. We override here so the surface grows
  // with its content, capped only by the viewport.
  tooltipSurface: {
    maxWidth: 'min(800px, calc(100vw - 64px))',
    width: 'max-content',
    minWidth: '420px',
  },
  tooltipBody: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    width: '100%',
    boxSizing: 'border-box',
  },
  tooltipHeader: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
  },
  tooltipSection: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    minWidth: 0,
  },
  sectionLabel: {
    fontSize: tokens.fontSizeBase100,
    fontWeight: tokens.fontWeightSemibold,
    color: tokens.colorNeutralForeground3,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  endpointText: {
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    overflowWrap: 'anywhere',
  },
  flagsRow: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXS,
  },
  paramsBlock: {
    margin: 0,
    padding: tokens.spacingHorizontalXS,
    backgroundColor: tokens.colorNeutralBackground2,
    borderRadius: tokens.borderRadiusSmall,
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    whiteSpace: 'pre-wrap',
    wordBreak: 'normal',
    overflowWrap: 'anywhere',
    maxHeight: '200px',
    maxWidth: '100%',
    overflowY: 'auto',
    overflowX: 'auto',
    boxSizing: 'border-box',
  },
  /** A single inner target entry in the tooltip's Inner Targets section. */
  innerTargetItem: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    padding: `${tokens.spacingVerticalXXS} ${tokens.spacingHorizontalXS}`,
    backgroundColor: tokens.colorNeutralBackground2,
    borderRadius: tokens.borderRadiusSmall,
  },
})
