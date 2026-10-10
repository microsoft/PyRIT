import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useFindingEvidenceListStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, marginTop: tokens.spacingVerticalM },
  region: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM },
  list: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalM, margin: 0 },
  item: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, overflowWrap: 'anywhere' },
  actions: {
    display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS,
    marginTop: tokens.spacingVerticalXS,
  },
  button: { ...mobileTouchTarget },
})
