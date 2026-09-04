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
    { name: 'key', type_name: 'str', required: true, description: 'Encryption key' },
    { name: 'append_description', type_name: 'bool', required: false, default: 'false' },
    { name: 'mode', type_name: 'str', required: false, choices: ['fast', 'safe'] },
    { name: 'file_path', type_name: 'str', required: false, description: 'Input file path' },
  ],
}

function renderParams() {
  return render(
    <FluentProvider theme={webLightTheme}>
      <ConverterParams
        converter={converter}
        paramValues={{}}
        paramsExpanded
        showValidation={false}
        onParamChange={jest.fn()}
        onFileBrowse={jest.fn()}
        onToggleExpanded={jest.fn()}
      />
    </FluentProvider>,
  )
}

describe('ConverterParams accessibility', () => {
  it('associates visible parameter names with every control type', () => {
    renderParams()

    expect(screen.getByRole('textbox', { name: /key/i })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: /append_description/i })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: /mode/i })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: /file_path/i })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /browse for file_path/i })).toBeInTheDocument()
  })
})
