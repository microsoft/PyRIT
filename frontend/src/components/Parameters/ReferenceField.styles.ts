import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useReferenceFieldStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', minWidth: 0, gap: tokens.spacingVerticalS },
  control: { width: '100%', minWidth: 0, '& > select': { width: '100%', minWidth: 0 } },
  row: { display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: tokens.spacingHorizontalS },
  action: { ...mobileTouchTargetHeight },
})
