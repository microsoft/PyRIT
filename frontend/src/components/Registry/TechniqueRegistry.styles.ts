import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useTechniqueRegistryStyles = makeStyles({
  root: {
    height: '100%', overflowY: 'auto', padding: tokens.spacingVerticalXXL,
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalL,
    [NARROW_VIEWPORT_QUERY]: { padding: tokens.spacingVerticalM },
  },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalM },
  header: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  table: { overflowX: 'auto' },
  tags: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalXS },
  configuration: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: tokens.fontFamilyMonospace },
  action: { ...mobileTouchTargetHeight },
  content: { maxHeight: '65vh', overflowY: 'auto' },
})
