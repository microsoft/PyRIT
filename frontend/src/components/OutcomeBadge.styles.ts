import { makeStyles, shorthands, tokens } from '@fluentui/react-components'

import { OUTCOME_PALETTE } from '@/styles/outcomePalette'
import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useOutcomeBadgeStyles = makeStyles({
  success: {
    '--pyrit-outcome-color': OUTCOME_PALETTE.success.color,
    '--pyrit-outcome-tint': OUTCOME_PALETTE.success.tint,
  },
  failure: {
    '--pyrit-outcome-color': OUTCOME_PALETTE.failure.color,
    '--pyrit-outcome-tint': OUTCOME_PALETTE.failure.tint,
  },
  error: {
    '--pyrit-outcome-color': OUTCOME_PALETTE.error.color,
    '--pyrit-outcome-tint': OUTCOME_PALETTE.error.tint,
  },
  undetermined: {
    '--pyrit-outcome-color': OUTCOME_PALETTE.undetermined.color,
    '--pyrit-outcome-tint': OUTCOME_PALETTE.undetermined.tint,
  },
  in_progress: {
    '--pyrit-outcome-color': tokens.colorBrandForeground1,
    '--pyrit-outcome-tint': tokens.colorBrandBackground2,
  },
  preparing: {
    '--pyrit-outcome-color': tokens.colorNeutralForeground2,
    '--pyrit-outcome-tint': tokens.colorNeutralBackground3,
  },
  filled: {
    backgroundColor: 'var(--pyrit-outcome-color)',
    ...shorthands.borderColor('var(--pyrit-outcome-color)'),
    color: tokens.colorNeutralBackground1,
  },
  tint: {
    backgroundColor: 'var(--pyrit-outcome-tint)',
    ...shorthands.borderColor('transparent'),
    color: 'var(--pyrit-outcome-color)',
  },
  outline: {
    backgroundColor: 'transparent',
    ...shorthands.borderColor('var(--pyrit-outcome-color)'),
    color: 'var(--pyrit-outcome-color)',
  },
  ghost: {
    backgroundColor: 'transparent',
    ...shorthands.borderColor('transparent'),
    color: 'var(--pyrit-outcome-color)',
  },
  icon: {
    display: 'inline-flex',
    flexShrink: 0,
    color: 'var(--pyrit-outcome-color)',
  },
  scoreTrigger: {
    ...mobileTouchTargetHeight,
    minWidth: 0,
    minHeight: 0,
    padding: 0,
  },
})
