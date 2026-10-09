import { makeStyles, tokens } from '@fluentui/react-components'

import { MINIMUM_TOUCH_TARGET_SIZE, mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useOperationsStyles = makeStyles({
  root: {
    flex: 1,
    minHeight: 0,
    overflowY: 'auto',
    backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
    padding: tokens.spacingHorizontalXXL,
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    [NARROW_VIEWPORT_QUERY]: { padding: tokens.spacingHorizontalM },
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: tokens.spacingHorizontalM,
  },
  heading: { margin: 0, overflowWrap: 'anywhere', minWidth: 0 },
  link: {
    display: 'inline-flex',
    alignItems: 'center',
    alignSelf: 'flex-start',
    gap: tokens.spacingHorizontalXS,
    minHeight: MINIMUM_TOUCH_TARGET_SIZE,
    color: tokens.colorBrandForegroundLink,
    textDecorationLine: 'none',
    overflowWrap: 'anywhere',
    ':hover': { textDecorationLine: 'underline' },
    ':focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: '2px',
    },
  },
  input: { width: '100%', minWidth: 0, ...mobileTouchTargetHeight },
  form: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalL },
  actions: { display: 'flex', justifyContent: 'end', gap: tokens.spacingHorizontalS },
  list: { margin: 0, padding: 0, listStyleType: 'none' },
  item: {
    paddingTop: tokens.spacingVerticalL,
    paddingBottom: tokens.spacingVerticalL,
    borderBottom: `${tokens.strokeWidthThin} solid ${tokens.colorNeutralStroke2}`,
    overflowWrap: 'anywhere',
  },
  title: { margin: 0, fontSize: tokens.fontSizeBase400, fontWeight: tokens.fontWeightSemibold },
  metadata: {
    display: 'flex',
    flexWrap: 'wrap',
    alignItems: 'center',
    gap: tokens.spacingHorizontalM,
    marginTop: tokens.spacingVerticalS,
  },
  description: { whiteSpace: 'pre-wrap', maxWidth: '75ch', marginBottom: 0 },
  findingActions: { marginTop: tokens.spacingVerticalM },
  table: {
    minWidth: '100%',
    tableLayout: 'auto',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  colName: { minWidth: '200px' },
  colFindings: { minWidth: '240px' },
  colDate: { minWidth: '160px', whiteSpace: 'nowrap' },
  badgeGroup: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalXXS },
  muted: { color: tokens.colorNeutralForeground3 },
  button: { ...mobileTouchTargetHeight },
})
