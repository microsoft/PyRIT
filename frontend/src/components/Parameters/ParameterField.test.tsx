import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ParameterField from './ParameterField'

describe('ParameterField help', () => {
  beforeEach(() => jest.clearAllMocks())

  it.each(['str', 'int', 'float', 'Path'])('should not show a raw %s type hint', (typeName: string) => {
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{ name: 'Setting', type_name: typeName, required: false }}
        value="" disabled={false} onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.queryByText(typeName)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Setting')).toBeEnabled()
  })

  it('should expose the declared description through hover help and the control description', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{
        name: 'Objective', type_name: 'str', required: true,
        description: 'The behavior you want to test.',
      }} value="" disabled={false} onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.getByRole('textbox', { name: 'Objective' })).toBeRequired()
    expect(screen.getByRole('textbox', { name: 'Objective' })).toHaveAccessibleDescription('The behavior you want to test.')
    await user.hover(screen.getByText('Objective'))
    expect(await screen.findByRole('tooltip')).toHaveTextContent('The behavior you want to test.')
  })

  it('should keep list syntax and default guidance visible', () => {
    render(<FluentProvider theme={webLightTheme}>
      <ParameterField parameter={{
        name: 'Examples', type_name: 'list[str]', is_list: true, required: false, default: ['one', 'two'],
      }} value="" disabled={false} showDefaultHint onChange={jest.fn()} />
    </FluentProvider>)

    expect(screen.getByText(/Defaults to one, two\. Comma-separated list of values\./)).toBeVisible()
  })
})
