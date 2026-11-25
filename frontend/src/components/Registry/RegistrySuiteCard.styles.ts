import { makeStyles } from '@fluentui/react-components'

import { mobileTouchTarget } from '@/styles/touchTargets'

export const useRegistrySuiteCardStyles = makeStyles({
  touchTarget: {
    ...mobileTouchTarget,
  },
})
