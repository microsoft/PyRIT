import { makeStyles } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useFeedbackDialogLoadingStyles = makeStyles({
  cancelButton: {
    ...mobileTouchTargetHeight,
  },
})
