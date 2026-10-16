import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScenarioDatasetTableStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  controls: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalM },
  browseLink: { color: tokens.colorBrandForegroundLink, ...mobileTouchTarget, display: 'inline-flex', alignItems: 'center' },
  scroll: { overflowX: 'auto' },
  table: { width: '100%', minWidth: '36em', borderCollapse: 'collapse' },
  toolbar: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalM },
  metadata: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalXS },
  chip: { ...mobileTouchTarget, padding: tokens.spacingHorizontalXXS, minWidth: 'unset' },
  actionsCell: { width: '3em' },
  datasetName: { overflowWrap: 'anywhere' },
  limitButton: {
    ...mobileTouchTarget,
    justifyContent: 'flex-start',
    paddingLeft: tokens.spacingHorizontalXS,
    paddingRight: tokens.spacingHorizontalXS,
    color: tokens.colorBrandForeground1,
    ':hover': { textDecorationLine: 'underline' },
  },
  limitEditor: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM, maxWidth: '24em' },
  totalLimit: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalXS },
  loadingRule: { display: 'inline-flex' },
  button: { ...mobileTouchTarget },
})
