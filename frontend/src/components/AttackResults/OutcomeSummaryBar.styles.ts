import { makeStyles, tokens } from '@fluentui/react-components'

import { OUTCOME_PALETTE } from '@/styles/outcomePalette'
import { mobileTouchTargetHeight } from '@/styles/touchTargets'

function outcomeFill(color: string) {
  return {
    backgroundColor: color,
    ':hover': { backgroundColor: color },
    ':active': { backgroundColor: color },
  }
}

export const useOutcomeSummaryBarStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalXS, minWidth: 0 },
  bar: {
    display: 'flex',
    width: '100%',
    height: tokens.spacingVerticalL,
    border: `1px solid ${tokens.colorNeutralStroke1}`,
    backgroundColor: tokens.colorNeutralBackground4,
    ...mobileTouchTargetHeight,
  },
  segment: {
    minWidth: 0,
    height: '100%',
    padding: 0,
    border: 0,
    borderRadius: 0,
    '@media (forced-colors: active)': { borderRight: '1px solid currentColor' },
  },
  legend: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalXS },
  swatch: { width: tokens.spacingHorizontalS, height: tokens.spacingVerticalS, border: '1px solid currentColor' },
  button: { columnGap: tokens.spacingHorizontalXS, ...mobileTouchTargetHeight },
  success: outcomeFill(OUTCOME_PALETTE.success.color),
  failure: outcomeFill(OUTCOME_PALETTE.failure.color),
  error: outcomeFill(OUTCOME_PALETTE.error.color),
  undetermined: outcomeFill(OUTCOME_PALETTE.undetermined.color),
})
