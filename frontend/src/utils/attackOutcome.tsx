import { tokens } from '@fluentui/react-components'
import {
  CheckmarkCircleRegular,
  DismissCircleRegular,
  QuestionCircleRegular,
  ErrorCircleRegular,
} from '@fluentui/react-icons'
import type { AttackOutcome } from '../types'

// Shared presentation for an attack's outcome, used by both the history table
// and the chat verdict chip so the two views stay visually consistent.

export const OUTCOME_ICONS: Record<AttackOutcome, React.ReactElement> = {
  success: <CheckmarkCircleRegular style={{ color: tokens.colorPaletteGreenForeground1 }} />,
  failure: <DismissCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  error: <ErrorCircleRegular style={{ color: tokens.colorPaletteRedForeground1 }} />,
  undetermined: <QuestionCircleRegular style={{ color: tokens.colorNeutralForeground3 }} />,
}

export const OUTCOME_COLORS: Record<AttackOutcome, 'success' | 'danger' | 'informative' | 'warning'> = {
  success: 'success',
  failure: 'danger',
  error: 'warning',
  undetermined: 'informative',
}

export function resolveOutcome(outcome?: AttackOutcome | null): AttackOutcome {
  return outcome ?? 'undetermined'
}
