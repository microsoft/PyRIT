import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

// Fluent's medium Checkbox indicator. With its inline margins and the label's padding, a row's title starts after it.
const CHECKBOX_INDICATOR_SIZE = '16px'
const TITLE_INDENT = `calc(${tokens.spacingHorizontalS} * 2 + ${CHECKBOX_INDICATOR_SIZE} + ${tokens.spacingHorizontalXS})`

export const useFindingExportDialogStyles = makeStyles({
  content: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  messageBar: { flexShrink: 0 },
  list: { margin: 0, padding: 0, listStyleType: 'none' },
  // Sub-findings indent under their parent, lined up with its title.
  subList: { margin: 0, padding: 0, paddingInlineStart: TITLE_INDENT, listStyleType: 'none' },
  // Read to screen readers; the indent and the locked check already show it on screen.
  visuallyHidden: {
    position: 'absolute',
    width: '1px',
    height: '1px',
    padding: 0,
    margin: '-1px',
    overflow: 'hidden',
    clip: 'rect(0, 0, 0, 0)',
    whiteSpace: 'nowrap',
    border: 0,
  },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: tokens.spacingHorizontalS, minWidth: 0 },
  checkbox: { flex: '1 1 12rem', minWidth: 0 },
  label: { minWidth: 0, overflowWrap: 'anywhere' },
  created: {
    color: tokens.colorNeutralForeground3,
    // On narrow screens the time gets its own line, lined up under the title.
    [NARROW_VIEWPORT_QUERY]: { flexBasis: '100%', paddingInlineStart: TITLE_INDENT },
  },
  button: { ...mobileTouchTargetHeight },
})
