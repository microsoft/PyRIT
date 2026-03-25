import { makeStyles, tokens } from '@fluentui/react-components'

export const useLabelsBarStyles = makeStyles({
  root: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXS,
    overflow: 'hidden',
    flex: '1 1 0',
    minWidth: 0,
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
  overflowBadge: {
    flexShrink: 0,
    cursor: 'pointer',
  },
  labelBadge: {
    display: 'flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
    padding: `2px ${tokens.spacingHorizontalS}`,
    borderRadius: tokens.borderRadiusMedium,
    cursor: 'pointer',
    userSelect: 'none' as const,
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
  },
})
