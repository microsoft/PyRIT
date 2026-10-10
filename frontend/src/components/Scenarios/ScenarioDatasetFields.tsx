import { Field, Input } from '@fluentui/react-components'

import type { RegisteredScenario } from '@/types'

import { useScenarioDatasetFieldsStyles } from './ScenarioDatasetFields.styles'
import { datasetSizeNotApplicable } from './scenarioConfigForm'

interface ScenarioDatasetFieldsProps {
  scenario: RegisteredScenario
  datasetOverride: string
  maxDatasetSize: string
  harmCategoriesFilter: string
  dataTypesFilter: string
  /** The scenario's own cap, shown as guidance so an operator can tell a default from an override. */
  configuredDefaultMaxDatasetSize: string
  disabled: boolean
  onDatasetOverrideChange: (value: string) => void
  onMaxDatasetSizeChange: (value: string) => void
  onHarmCategoriesFilterChange: (value: string) => void
  onDataTypesFilterChange: (value: string) => void
}

/** Dataset selection and filtering inputs shared by the launch form and the preset editor. */
export default function ScenarioDatasetFields({
  scenario,
  datasetOverride,
  maxDatasetSize,
  harmCategoriesFilter,
  dataTypesFilter,
  configuredDefaultMaxDatasetSize,
  disabled,
  onDatasetOverrideChange,
  onMaxDatasetSizeChange,
  onHarmCategoriesFilterChange,
  onDataTypesFilterChange,
}: ScenarioDatasetFieldsProps) {
  const styles = useScenarioDatasetFieldsStyles()
  const notApplicable = datasetSizeNotApplicable(scenario)

  return (
    <>
      <Field
        label="Dataset override"
        hint="Comma-separated dataset names. Leave blank to use the scenario's default datasets."
      >
        <Input
          className={styles.control}
          value={datasetOverride}
          disabled={disabled}
          onChange={(_, data) => onDatasetOverrideChange(data.value)}
          placeholder={scenario.default_datasets.join(', ') || undefined}
          data-testid="dataset-override-input"
        />
      </Field>
      <Field
        label="Max dataset size"
        hint={notApplicable
          ? 'This scenario uses prompt-generation limits instead of a dataset size limit.'
          : configuredDefaultMaxDatasetSize
          ? `The scenario default is ${configuredDefaultMaxDatasetSize}. Edit it to override the default.`
          : 'Enter a positive integer to limit the selected dataset size. Leave empty to use scenario defaults.'}
      >
        <Input
          className={styles.numberInput}
          type="number"
          min={1}
          value={maxDatasetSize}
          disabled={disabled || notApplicable}
          onChange={(_, data) => onMaxDatasetSizeChange(data.value)}
          data-testid="max-dataset-size-input"
        />
      </Field>
      <Field
        label="Harm categories"
        hint="Comma-separated values. A seed must match every listed category."
      >
        <Input
          className={styles.control}
          value={harmCategoriesFilter}
          disabled={disabled}
          placeholder="cyber, violence"
          onChange={(_, data) => onHarmCategoriesFilterChange(data.value)}
          data-testid="harm-categories-filter-input"
        />
      </Field>
      <Field
        label="Data types"
        hint="Comma-separated values. A seed can match any listed data type."
      >
        <Input
          className={styles.control}
          value={dataTypesFilter}
          disabled={disabled}
          placeholder="text, image_path"
          onChange={(_, data) => onDataTypesFilterChange(data.value)}
          data-testid="data-types-filter-input"
        />
      </Field>
    </>
  )
}
