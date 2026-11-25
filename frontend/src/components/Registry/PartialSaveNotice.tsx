import { MessageBar, MessageBarBody, MessageBarTitle } from '@fluentui/react-components'

interface PartialSaveNoticeProps {
  /** Says where the saved scenario is missing, such as "added to release_gate". */
  missingFrom: string
  error: string
}

/** Server details don't always end a sentence; the notice continues after them. */
function asSentence(text: string): string {
  const trimmed = text.trim()
  return /[.!?]$/.test(trimmed) ? trimmed : `${trimmed}.`
}

/** Says plainly that the scenario was saved but the second write failed, so Retry finishes it. */
export default function PartialSaveNotice({ missingFrom, error }: PartialSaveNoticeProps) {
  return (
    <MessageBar intent="warning" data-testid="partial-save-notice">
      <MessageBarBody>
        <MessageBarTitle>The scenario was saved but not {missingFrom}.</MessageBarTitle>
        {asSentence(error)} Select Retry to finish.
      </MessageBarBody>
    </MessageBar>
  )
}
