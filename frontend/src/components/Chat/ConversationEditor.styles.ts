import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useConversationEditorStyles = makeStyles({
  root: {
    display: 'flex', minHeight: 0, minWidth: 0, flexGrow: 1,
    [NARROW_VIEWPORT_QUERY]: { flexDirection: 'column' },
  },
  converterPane: {
    display: 'flex', minHeight: 0, flexShrink: 0,
    [NARROW_VIEWPORT_QUERY]: {
      flexBasis: '40%', maxHeight: '40%', width: '100%',
      '& > div': { maxWidth: '100%' },
    },
  },
  content: { display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0, flexGrow: 1 },
  thread: { overflowY: 'auto', padding: tokens.spacingHorizontalL, flexGrow: 1 },
  row: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  card: {
    maxWidth: '900px', marginInline: 'auto', minWidth: 0,
  },
  piece: { marginBlock: tokens.spacingVerticalXS, minWidth: 0 },
  toolPrompt: {
    padding: tokens.spacingHorizontalM, border: `1px solid ${tokens.colorNeutralStroke1}`,
    borderRadius: tokens.borderRadiusXLarge, backgroundColor: tokens.colorNeutralBackground3,
  },
  role: {
    color: tokens.colorNeutralForeground3, fontSize: tokens.fontSizeBase200, border: 0,
    backgroundColor: 'transparent', ...mobileTouchTarget,
  },
  headerSpacer: { flexGrow: 1 },
  insertRow: { display: 'flex', justifyContent: 'center', paddingBlock: tokens.spacingVerticalXXS },
  insertButton: {
    color: tokens.colorNeutralForeground3, fontWeight: tokens.fontWeightRegular,
    fontSize: tokens.fontSizeBase200, ...mobileTouchTarget,
  },
  footer: {
    display: 'flex', gap: tokens.spacingHorizontalM, alignItems: 'center', justifyContent: 'flex-end', flexWrap: 'wrap',
    padding: tokens.spacingHorizontalM, borderTop: `1px solid ${tokens.colorNeutralStroke1}`,
    [NARROW_VIEWPORT_QUERY]: { flexDirection: 'column', alignItems: 'stretch' },
  },
  button: { ...mobileTouchTarget },
  textarea: { width: '100%' },
})
