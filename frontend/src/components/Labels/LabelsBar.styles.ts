import { makeStyles, tokens } from '@fluentui/react-components'

export const useLabelsBarStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    overflow: 'hidden',
    // Always reserve enough room for the labels icon + count badge so it
    // stays visible at any ribbon width. `minWidth` is the width of the
    // icon button alone; the chip area beyond it grows when there's
    // additional space.
    flex: '1 1 auto',
    minWidth: '60px',
    position: 'relative',
  },
  iconButton: {
    flexShrink: 0,
    '@media (max-width: 600px)': {
      minHeight: '44px',
    },
  },
  iconTooltipBody: {
    whiteSpace: 'nowrap',
    minWidth: 'max-content',
  },
  labelsContainer: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    flexWrap: 'nowrap',
    overflow: 'hidden',
    flex: '1 1 0',
    minWidth: 0,
  },
  measureRow: {
    position: 'absolute',
    visibility: 'hidden',
    pointerEvents: 'none',
    whiteSpace: 'nowrap',
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    top: 0,
    left: 0,
  },
  labelBadge: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
    padding: `2px ${tokens.spacingHorizontalS}`,
    borderRadius: tokens.borderRadiusMedium,
    cursor: 'pointer',
    userSelect: 'none' as const,
    flexShrink: 0,
  },
  labelNormal: {
    backgroundColor: tokens.colorNeutralBackground3,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
  },
  labelDummy: {
    backgroundColor: tokens.colorPaletteYellowBackground2,
    border: `1px solid ${tokens.colorPaletteYellowBorder1}`,
  },
  removeBtn: {
    minWidth: '16px',
    width: '16px',
    height: '16px',
    padding: 0,
  },
  popoverSurface: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
    padding: tokens.spacingVerticalM,
    minWidth: '250px',
  },
  popoverDivider: {
    height: '1px',
    backgroundColor: tokens.colorNeutralStroke2,
    marginTop: tokens.spacingVerticalXS,
    marginBottom: tokens.spacingVerticalXS,
  },
  inputRow: {
    display: 'flex',
    gap: tokens.spacingHorizontalXS,
    alignItems: 'flex-start',
  },
  inputField: {
    flex: 1,
    minWidth: '80px',
  },
  suggestions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalXXS,
    maxHeight: '80px',
    overflowY: 'auto',
  },
  editDropdown: {
    position: 'absolute',
    top: '100%',
    left: 0,
    zIndex: 100,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalXXS,
    backgroundColor: tokens.colorNeutralBackground1,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusMedium,
    padding: tokens.spacingVerticalXS,
    boxShadow: tokens.shadow4,
    maxHeight: '120px',
    overflowY: 'auto',
    minWidth: '120px',
  },
  suggestionChip: {
    cursor: 'pointer',
    ':hover': {
      opacity: 0.8,
    },
  },
  errorText: {
    color: tokens.colorPaletteRedForeground1,
  },
  warningIcon: {
    color: tokens.colorPaletteYellowForeground2,
    display: 'flex',
    alignItems: 'center',
    flexShrink: 0,
  },
})
