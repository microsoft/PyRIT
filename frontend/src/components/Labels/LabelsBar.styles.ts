import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget } from '../../styles/touchTargets'

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
    ...mobileTouchTarget,
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
    padding: `0 ${tokens.spacingHorizontalS}`,
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
  labelEdit: {
    display: 'inline-flex',
    alignItems: 'center',
    gap: tokens.spacingHorizontalXXS,
    // The badge keeps the horizontal padding so the pill looks the same; the
    // vertical padding lives here so the whole height of it starts an edit.
    padding: '2px 0',
    background: 'none',
    border: 'none',
    margin: 0,
    font: 'inherit',
    color: 'inherit',
    cursor: 'pointer',
    userSelect: 'none' as const,
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
  // The picker is wider than the plain input it replaces, and the labels bar
  // clips what overflows. Let it shrink rather than lose its chevron: Fluent
  // puts an intrinsic min-width on both the root and the inner input.
  operationPicker: {
    width: '180px',
    minWidth: 0,
    maxWidth: '100%',
    '& input': {
      minWidth: 0,
    },
  },
  // Caps the list so it stays under the input instead of stretching to fill
  // the window. This only takes effect because the picker asks Fluent to
  // auto-size width alone; by default it writes its own max-height inline,
  // which beats this rule. Asking for width alone also gives up Fluent's
  // vertical fitting, so the cap yields to the viewport when it has to.
  operationListbox: {
    maxHeight: 'min(240px, calc(100vh - 32px))',
  },
  // Fluent dims disabled options to ~1.9:1 contrast, which is too faint for
  // text the user has to read. These are messages, not choices.
  operationNote: {
    color: tokens.colorNeutralForeground2,
  },
  operationNoteError: {
    color: tokens.colorPaletteRedForeground1,
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
