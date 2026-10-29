import type { LabelProps } from '@fluentui/react-components'

import ContextualHelpLabel from '@/components/ContextualHelpLabel'

const SCANNER_VISIBILITY_TIP = 'Controls whether this preset appears as its own entry in the Scanner. '
  + 'A preset used only within a scenario suite can stay hidden; the suite still runs it.'

/** The "Add to scanner" label, with a tip explaining what hiding a preset from the Scanner means. */
export default function ScannerVisibilityLabel(props: LabelProps) {
  return <ContextualHelpLabel term="Add to scanner" labelProps={props}>{SCANNER_VISIBILITY_TIP}</ContextualHelpLabel>
}
