import React from 'react'
import { Combobox, Field, Option } from '@fluentui/react-components'
import { useConverterPanelStyles } from './ConverterPanel.styles'

interface ConverterGroup {
  type: string
  converters: { converter_type: string; is_llm_based?: boolean }[]
}

export interface SelectConverterInputProps {
  query: string
  selectedConverterType: string
  groupedConverters: ConverterGroup[]
  onOptionSelect: (type: string, text: string) => void
  onQueryChange: (value: string) => void
}

export default function SelectConverterInput({ query, selectedConverterType, groupedConverters, onOptionSelect, onQueryChange }: SelectConverterInputProps) {
  const styles = useConverterPanelStyles()

  return (
    <Field label="Converter">
      <Combobox
        value={query}
        selectedOptions={selectedConverterType ? [selectedConverterType] : []}
        onOptionSelect={(_, data) => {
          onOptionSelect(data.optionValue ?? '', data.optionText ?? '')
        }}
        onChange={(e) => onQueryChange((e.target as HTMLInputElement).value)}
        placeholder="Search converters..."
        data-testid="converter-panel-select"
      >
        {groupedConverters.map((group) => (
          <React.Fragment key={group.type}>
            <Option key={`__header_${group.type}`} text="" disabled value="">
              <span className={`${styles.groupHeader} ${styles[`header_${group.type}` as keyof typeof styles] ?? ''}`}>
                — {group.type.replace('_path', '')} output —
              </span>
            </Option>
            {group.converters.map((converter) => (
              <Option key={converter.converter_type} value={converter.converter_type} text={converter.converter_type} data-testid={`converter-option-${converter.converter_type}`}>
                <span className={styles.optionContent}>
                  {converter.converter_type}
                  {converter.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                </span>
              </Option>
            ))}
          </React.Fragment>
        ))}
      </Combobox>
    </Field>
  )
}
