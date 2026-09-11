import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import ConverterParams from './ConverterParams'
import type { ConverterCatalogEntry } from '../../../types'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const converter: ConverterCatalogEntry = {
  converter_type: 'VigenereConverter',
  supported_input_types: ['text'],
  supported_output_types: ['text'],
  is_llm_based: false,
  description: 'Vigenere cipher.',
  parameters: [
    { name: 'key', type_name: 'str', required: true, default: null, choices: null, description: 'Cipher key.' },
    { name: 'append_description', type_name: 'bool', required: false, default: 'false', choices: null, description: 'Append description.' },
    { name: 'mode', type_name: 'str', required: false, default: 'fast', choices: ['fast', 'slow'], description: 'Speed mode.' },
    { name: 'template_file_path', type_name: 'str', required: false, default: null, choices: null, description: 'Path to template.' },
  ],
}

function renderParams(overrides: Partial<React.ComponentProps<typeof ConverterParams>> = {}) {
  return render(
    <TestWrapper>
      <ConverterParams
        converter={converter}
        paramValues={{}}
        paramsExpanded
        showValidation={false}
        onParamChange={jest.fn()}
        onFileBrowse={jest.fn()}
        onToggleExpanded={jest.fn()}
        {...overrides}
      />
    </TestWrapper>,
  )
}

describe('ConverterParams accessible names', () => {
  it('exposes text, boolean, choice, and file controls by their visible parameter names', () => {
    renderParams()

    expect(screen.getByRole('textbox', { name: 'key' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: 'append_description' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'mode' })).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'template_file_path' })).toBeInTheDocument()
  })

  it('keeps required, error, and type hint associated with the text control', () => {
    renderParams({ showValidation: true })

    const key = screen.getByRole('textbox', { name: 'key' })
    expect(key).toBeRequired()
    expect(key).toBeInvalid()
    expect(key).toHaveAccessibleDescription(/Required/)
    expect(key).toHaveAccessibleDescription(/str/)
  })

  it('keeps boolean state visible without using it as the accessible name', () => {
    renderParams({ paramValues: { append_description: 'true' } })

    expect(screen.getByRole('switch', { name: 'append_description' })).toBeChecked()
    expect(screen.getByText('True')).toBeInTheDocument()
  })
})
