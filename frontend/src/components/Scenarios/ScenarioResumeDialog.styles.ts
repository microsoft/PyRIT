import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useScenarioResumeDialogStyles = makeStyles({
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
  },
  runId: {
    overflowWrap: 'anywhere',
  },
  numberInput: {
    ...mobileTouchTargetHeight,
  },
  button: {
    ...mobileTouchTarget,
  },
})
