import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'
import { WORKSPACE_CANVAS_BACKGROUND } from '@/styles/workspaceBackground'

export const useTechniqueRegistryStyles = makeStyles({
  root: {
    display: 'flex', flexDirection: 'column', height: '100%', width: '100%',
    minWidth: 0, maxWidth: '100%', overflowX: 'hidden', overflowY: 'auto',
    padding: tokens.spacingVerticalXXL, backgroundColor: WORKSPACE_CANVAS_BACKGROUND,
    [NARROW_VIEWPORT_QUERY]: { padding: `${tokens.spacingVerticalL} ${tokens.spacingHorizontalM}` },
  },
  header: {
    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
    flexWrap: 'wrap', minWidth: 0, gap: tokens.spacingVerticalM, marginBottom: tokens.spacingVerticalXL,
    [NARROW_VIEWPORT_QUERY]: { flexDirection: 'column', alignItems: 'stretch' },
  },
  headerText: { display: 'flex', flexDirection: 'column', minWidth: 0, gap: tokens.spacingVerticalXS },
  subtitle: { color: tokens.colorNeutralForeground3 },
  headerActions: {
    display: 'flex', flexWrap: 'wrap', minWidth: 0, gap: tokens.spacingHorizontalS,
    [NARROW_VIEWPORT_QUERY]: { width: '100%' },
  },
  headerAction: {
    ...mobileTouchTargetHeight,
    [NARROW_VIEWPORT_QUERY]: { flex: '1 1 8rem' },
  },
  filters: {
    display: 'flex', flexWrap: 'wrap', alignItems: 'center',
    gap: tokens.spacingHorizontalM, marginBottom: tokens.spacingVerticalL,
  },
  table: { flex: 1, minHeight: 0, minWidth: 0, maxWidth: '100%', overflow: 'auto' },
  tableContent: { tableLayout: 'fixed', width: '100%', minWidth: '48rem' },
  tableHeader: { position: 'sticky', top: 0, backgroundColor: tokens.colorNeutralBackground1, zIndex: 1 },
  nameCell: { overflowWrap: 'anywhere', wordBreak: 'break-word' },
  tags: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalXS },
  creationCall: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: tokens.fontFamilyMonospace },
  action: { ...mobileTouchTargetHeight },
  content: { maxHeight: '65vh', overflowY: 'auto' },
})
