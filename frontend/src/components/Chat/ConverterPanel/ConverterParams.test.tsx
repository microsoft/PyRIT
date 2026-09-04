import React from 'react'
import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import type { ConverterCatalogEntry } from '../../../types'
import ConverterParams from './ConverterParams'

const converter: ConverterCatalogEntry = {
  converter_type: 'TestConverter',
  supported_input_types: ['text'],
  supported_output_types: ['text'],
  is_llm_based: false,
  parameters: [
    { name: 'key', type_name: 'str', required: true, default: null, description: 'Cipher key' },
    { name: 'append_description', type_name: 'bool', required: false, default: 'false' },
    { name: 'mode', type_name: 'str', required: false, default: 'safe', choices: ['safe', 'strict'] },
    { name: 'image_path', type_name: 'str', required: false, default: null, description: 'Image file path' },
  ],
}

function renderParams(showValidation = false) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <ConverterParams
        converter={converter}
        paramValues={{}}
        paramsExpanded
        showValidation={showValidation}
        onParamChange={jest.fn()}
        onFileBrowse={jest.fn()}
        onToggleExpanded={jest.fn()}
      />
    </FluentProvider>,
  )
}

describe('ConverterParams accessibility', () => {
  it('associates visible parameter names with every control variant', () => {
    renderParams()

    expect(screen.getByRole('textbox', { name: 'key' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'append_description' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'mode' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'image_path' })).toBeInTheDocument()
  })

  it('connects required validation text and state to the input', () => {
    renderParams(true)

    const keyInput = screen.getByRole('textbox', { name: 'key' })
    expect(keyInput).toHaveAttribute('aria-required', 'true')
    expect(keyInput).toHaveAttribute('aria-invalid', 'true')
    expect(keyInput).toHaveAccessibleDescription(/Required/)
  })
})
