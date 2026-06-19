import { useState } from 'react'
import { Button, Caption1, Textarea, mergeClasses } from '@fluentui/react-components'
import { ChevronDownRegular, ChevronRightRegular, WarningRegular } from '@fluentui/react-icons'
import { useSystemPromptSetupStyles } from './SystemPromptSetup.styles'

const SYSTEM_PROMPT_SOFT_LIMIT = 2000

interface SystemPromptSetupProps {
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  disabledReason?: string
}

export default function SystemPromptSetup({ value, onChange, disabled = false, disabledReason }: SystemPromptSetupProps) {
  const styles = useSystemPromptSetupStyles()
  const [expanded, setExpanded] = useState(false)
  const overLimit = value.length > SYSTEM_PROMPT_SOFT_LIMIT

  return (
    <div className={styles.root} data-testid="system-prompt-setup">
      <div className={styles.headerRow}>
        <Button
          appearance="transparent"
          size="small"
          icon={expanded ? <ChevronDownRegular /> : <ChevronRightRegular />}
          onClick={() => setExpanded(prev => !prev)}
          className={styles.header}
          data-testid="toggle-system-prompt-btn"
          aria-expanded={expanded}
          disabled={disabled}
        >
          System Prompt
        </Button>
        {disabled && disabledReason && (
          <span className={styles.reason} data-testid="system-prompt-reason">
            <WarningRegular fontSize={14} />
            <Caption1>{disabledReason}</Caption1>
          </span>
        )}
      </div>
      {expanded && !disabled && (
        <div className={styles.body}>
          <Textarea
            value={value}
            onChange={(_, data) => onChange(data.value)}
            resize="vertical"
            placeholder="Sent as the first message of the conversation."
            aria-label="System prompt"
            className={styles.textareaRoot}
            textarea={{ className: styles.textareaInner }}
            data-testid="system-prompt-input"
          />
          <Caption1
            className={mergeClasses(styles.counter, overLimit && styles.counterOver)}
            data-testid="system-prompt-counter"
          >
            {value.length.toLocaleString()} characters
          </Caption1>
        </div>
      )}
    </div>
  )
}
