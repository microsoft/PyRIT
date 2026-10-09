import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingEvidenceListStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  list: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  item: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, overflowWrap: 'anywhere' },
  actions: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  button: { ...mobileTouchTarget },
})
