import { useState } from 'react'
import { createRoot } from 'react-dom/client'

import { Button, Field, FluentProvider, webLightTheme } from '@fluentui/react-components'

import SingleStepSpinButton from '../../src/components/Parameters/SingleStepSpinButton'

export default function NumericControlsFixture() {
  const [value, setValue] = useState(0.5)
  return (
    <FluentProvider theme={webLightTheme}>
      <Field label="Fractional step">
        <SingleStepSpinButton
          value={value}
          step={0.1}
          min={0}
          max={1}
          onChange={(_, data) => setValue(data.value ?? Number(data.displayValue))}
        />
      </Field>
      <Field label="Disabled stepper">
        <SingleStepSpinButton value={2} disabled />
      </Field>
      <Button onClick={() => setValue(0.5)}>Reset</Button>
    </FluentProvider>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('Numeric control fixture root is missing.')
createRoot(root).render(<NumericControlsFixture />)
