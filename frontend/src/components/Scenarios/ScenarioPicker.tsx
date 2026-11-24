import { Combobox, Option } from '@fluentui/react-components'

import type { RegisteredScenario } from '@/types'

interface ScenarioPickerProps {
  scenarios: RegisteredScenario[]
  /** The selected scenario name, or an empty string for none. */
  value: string
  onChange: (scenarioName: string) => void
  disabled?: boolean
  className?: string
  testId?: string
}

/** Picks one registered scenario by name. Wrap it in a `Field` to label it. */
export default function ScenarioPicker({
  scenarios,
  value,
  onChange,
  disabled = false,
  className,
  testId,
}: ScenarioPickerProps) {
  return (
    <Combobox
      className={className}
      value={value}
      selectedOptions={value ? [value] : []}
      disabled={disabled}
      placeholder="Choose a scenario"
      onOptionSelect={(_, data) => {
        if (data.optionValue) {
          onChange(data.optionValue)
        }
      }}
      data-testid={testId}
    >
      {scenarios.map((scenario) => (
        <Option key={scenario.scenario_name} value={scenario.scenario_name}>
          {scenario.scenario_name}
        </Option>
      ))}
    </Combobox>
  )
}
