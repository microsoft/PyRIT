import { useState } from 'react'

import { Button, Field, Input, Select } from '@fluentui/react-components'
import type { InputOnChangeData, SelectOnChangeData } from '@fluentui/react-components'

import type { AttackAnalyticsDimension } from '@/types'
import { ANALYTICS_DIMENSIONS, isAnalyticsLabelKey } from '@/utils/attackAnalytics'

import { useAnalyticsDimensionPickerStyles } from './AnalyticsDimensionPicker.styles'

interface AnalyticsDimensionPickerProps {
  readonly label: string
  readonly dimension: AttackAnalyticsDimension | null
  readonly onChange: (dimension: AttackAnalyticsDimension | null) => void
}

/**
 * Built-in dimensions commit immediately; a custom label needs an explicit valid
 * key before lookup. null means an uncommitted draft: filter editors hide facets,
 * while chart controls retain their last valid axis until Use label is chosen.
 * Callers key this control by committed dimension to restore drafts on URL changes.
 */
export default function AnalyticsDimensionPicker({ label, dimension, onChange }: AnalyticsDimensionPickerProps) {
  const styles = useAnalyticsDimensionPickerStyles()
  const [labelKey, setLabelKey] = useState(dimension?.name === 'label' ? dimension.label_key : '')
  const [choosingLabel, setChoosingLabel] = useState(dimension?.name === 'label' || dimension === null)
  const selected = choosingLabel ? 'label'
    : dimension?.name === 'converter_type' ? `${dimension.converter_direction ?? 'request'}_converters`
      : dimension?.name ?? 'label'

  function selectDimension(_event: React.ChangeEvent<HTMLSelectElement>, data: SelectOnChangeData): void {
    setChoosingLabel(data.value === 'label')
    if (data.value === 'label') {
      onChange(null)
      return
    }
    const option = ANALYTICS_DIMENSIONS.find((item: typeof ANALYTICS_DIMENSIONS[number]) => item.value === data.value)
    if (option) onChange(option.dimension)
  }

  return (
    <div className={styles.root}>
      <Field label={label} className={styles.field}>
        <Select className={styles.input} value={selected} onChange={selectDimension}>
          {ANALYTICS_DIMENSIONS.map((option: typeof ANALYTICS_DIMENSIONS[number]) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
          <option value="label">Custom label</option>
        </Select>
      </Field>
      {choosingLabel && (
        <>
          <Field
            className={styles.field}
            label={`${label} label key`}
            validationState={labelKey && !isAnalyticsLabelKey(labelKey) ? 'error' : 'none'}
            validationMessage={labelKey && !isAnalyticsLabelKey(labelKey)
              ? 'Use letters, numbers, _, . or -. Operation and operator have dedicated filters.'
              : undefined}
          >
            <Input
              className={styles.input}
              name="analytics-label-key"
              autoComplete="off"
              spellCheck={false}
              value={labelKey}
              maxLength={128}
              onChange={(_event: React.ChangeEvent<HTMLInputElement>, data: InputOnChangeData) => {
                setLabelKey(data.value)
                onChange(null)
              }}
              onKeyDown={(event: React.KeyboardEvent<HTMLInputElement>) => {
                if (event.key === 'Enter' && isAnalyticsLabelKey(labelKey)) onChange({ name: 'label', label_key: labelKey })
              }}
            />
          </Field>
          <Button
            className={styles.button}
            disabled={!isAnalyticsLabelKey(labelKey)}
            onClick={() => { onChange({ name: 'label', label_key: labelKey }) }}
          >
            Use label
          </Button>
        </>
      )}
    </div>
  )
}
