import type { FormEvent } from 'react'

import {
  Button, Field, Popover, PopoverSurface, PopoverTrigger, Radio, RadioGroup, Text, Tooltip,
} from '@fluentui/react-components'
import type { RadioGroupOnChangeData } from '@fluentui/react-components'
import { AddRegular, SubtractRegular } from '@fluentui/react-icons'

import type { MultiSendOptions } from '@/types'

import { useMultiSendSettingsStyles } from './MultiSendSettings.styles'

const MAX_SEND_COUNT = 10

interface MultiSendSettingsProps {
  options: MultiSendOptions
  disabled: boolean
  onChange: (options: MultiSendOptions) => void
}

export default function MultiSendSettings({ options, disabled, onChange }: MultiSendSettingsProps) {
  const styles = useMultiSendSettingsStyles()
  const handleConverterChange = (_event: FormEvent<HTMLDivElement>, data: RadioGroupOnChangeData): void => {
    if (data.value === 'shared' || data.value === 'per_branch') {
      onChange({ ...options, requestConverterMode: data.value })
    }
  }

  return (
    <Popover positioning="above-end">
      <PopoverTrigger disableButtonEnhancement>
        <Tooltip content="Choose how many conversations receive this prompt" relationship="description">
          <Button
            appearance={options.count > 1 ? 'secondary' : 'subtle'}
            size="small"
            className={styles.touchTarget}
            disabled={disabled}
            aria-label={`Repetitions: ${options.count}`}
            data-testid="multi-send-settings"
          >
            n={options.count}
          </Button>
        </Tooltip>
      </PopoverTrigger>
      <PopoverSurface aria-label="Prompt repetitions">
        <div className={styles.content}>
          <Text weight="semibold">Prompt repetitions</Text>
          <div className={styles.count}>
            <Button
              icon={<SubtractRegular />}
              className={styles.touchTarget}
              aria-label="Decrease repetitions"
              disabled={disabled || options.count <= 1}
              onClick={() => onChange({ ...options, count: options.count - 1 })}
            />
            <Text aria-live="polite">{options.count} of {MAX_SEND_COUNT}</Text>
            <Button
              icon={<AddRegular />}
              className={styles.touchTarget}
              aria-label="Increase repetitions"
              disabled={disabled || options.count >= MAX_SEND_COUNT}
              onClick={() => onChange({ ...options, count: options.count + 1 })}
            />
          </div>
          <Text size={200}>
            {options.count === 1
              ? 'Send once in this conversation.'
              : `Keep this conversation and create ${options.count - 1} copies of its history. Send the next prompt in each.`}
            {' '}The count resets to 1 after sending.
          </Text>
          <Field label="Request converters">
            <RadioGroup value={options.requestConverterMode} onChange={handleConverterChange} disabled={disabled}>
              <Radio value="shared" label="Convert once, reuse for all" />
              <Radio value="per_branch" label="Convert independently for each" />
            </RadioGroup>
          </Field>
          <Text size={200}>
            Single sends and shared repeats use only applied conversions. Use Add converted value before sending.
            {' '}Independent repeats run unapplied pipelines on original inputs. Applied previews stay unchanged in either mode.
          </Text>
        </div>
      </PopoverSurface>
    </Popover>
  )
}
