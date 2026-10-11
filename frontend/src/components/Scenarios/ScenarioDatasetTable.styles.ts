import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useScenarioDatasetTableStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  controls: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalM },
  scroll: { overflowX: 'auto' },
  table: { width: '100%', borderCollapse: 'collapse' },
  cell: { padding: tokens.spacingHorizontalS, textAlign: 'left' },
  input: { maxWidth: '12em' },
  button: { ...mobileTouchTarget },
})
