import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingEvidenceListStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, marginTop: tokens.spacingVerticalM },
  region: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  tableScroll: { maxWidth: '100%', overflowX: 'auto' },
  table: { minWidth: '640px', tableLayout: 'auto' },
  textColumn: { minWidth: '140px' },
  cell: { verticalAlign: 'top', overflowWrap: 'anywhere' },
  stack: {
    display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: tokens.spacingVerticalXXS,
    paddingBlock: tokens.spacingVerticalS,
  },
  actions: {
    display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS,
    marginTop: tokens.spacingVerticalXS,
  },
  rowActions: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS },
  header: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS },
  headerActions: { display: 'flex', gap: tokens.spacingHorizontalS, marginLeft: 'auto' },
  button: { ...mobileTouchTarget },
})
