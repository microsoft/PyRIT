import { Dropdown, Option, OptionGroup, Text } from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'

import type { ConverterInstance } from '@/types'

import { useConverterPanelStyles } from './ConverterPanel.styles'

const CREATE_CONVERTER_OPTION = '__create_new_converter__'

interface ConverterGroup {
  type: string
  converters: ConverterInstance[]
}

export interface SelectConverterInputProps {
  groupedConverters: ConverterGroup[]
  onOptionSelect: (converterId: string) => void
  onCreateNew: () => void
}

function formatDataType(dataType: string): string {
  return dataType.replace('_path', '').replace(/_/g, ' ')
}

export default function SelectConverterInput({
  groupedConverters,
  onOptionSelect,
  onCreateNew,
}: SelectConverterInputProps) {
  const styles = useConverterPanelStyles()

  return (
    <Dropdown
      aria-label="Add converter"
      className={styles.converterPicker}
      inlinePopup
      listbox={{ className: styles.converterPickerListbox }}
      placeholder="Add converter..."
      positioning={{ matchTargetSize: 'width' }}
      selectedOptions={[]}
      value=""
      onOptionSelect={(_, data) => {
        if (data.optionValue === CREATE_CONVERTER_OPTION) {
          onCreateNew()
        } else if (data.optionValue) {
          onOptionSelect(data.optionValue)
        }
      }}
      data-testid="converter-panel-select"
    >
      <Option
        text="New converter"
        value={CREATE_CONVERTER_OPTION}
        data-testid="create-converter-option"
      >
        <div className={styles.createOption}>
          <AddRegular />
          <div className={styles.optionText}>
            <Text weight="semibold">New converter</Text>
            <Text size={200} className={styles.hintText}>
              Configure a converter and add it to the registry.
            </Text>
          </div>
        </div>
      </Option>
      {groupedConverters.map((group) => (
        <OptionGroup key={group.type} label={`${formatDataType(group.type)} output`}>
          {group.converters.map((converter) => {
            const description = converter.description || 'No description is available.'
            const accessibleDescription = [
              converter.converter_id,
              converter.identifier.class_name,
              description,
              converter.is_llm_based ? 'LLM' : undefined,
            ].filter((value): value is string => Boolean(value)).join('. ')

            return (
              <Option
                aria-label={accessibleDescription}
                key={converter.converter_id}
                value={converter.converter_id}
                text={converter.converter_id}
                data-testid={`converter-option-${converter.converter_id}`}
              >
                <div className={styles.registeredOption}>
                  <div className={styles.optionHeader}>
                    <Text weight="semibold">{converter.converter_id}</Text>
                    <div className={styles.optionBadges}>
                      <Text size={200} className={styles.optionType}>
                        {converter.identifier.class_name}
                      </Text>
                      {converter.is_llm_based && <span className={styles.llmBadge}>LLM</span>}
                    </div>
                  </div>
                  <Text size={200} className={styles.hintText}>
                    {description}
                  </Text>
                </div>
              </Option>
            )
          })}
        </OptionGroup>
      ))}
    </Dropdown>
  )
}
