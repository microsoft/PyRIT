import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, FluentProvider, Input, webLightTheme } from '@fluentui/react-components'

import HelpField from './HelpField'

const HELP = 'The model or endpoint you are testing.'

function ExampleField() {
  return (
    <FluentProvider theme={webLightTheme}>
      <HelpField label="Objective target" help={HELP} required hint="Use a registered name."
        validationMessage="Choose an available target.">
        <Input />
      </HelpField>
      <Button>Next control</Button>
    </FluentProvider>
  )
}

describe('HelpField', () => {
  beforeEach(() => jest.clearAllMocks())

  it('should retain the label, description, required state, and visible validation without an information button', () => {
    render(<ExampleField />)

    const input = screen.getByRole('textbox', { name: 'Objective target' })
    expect(input).toBeRequired()
    expect(input).toBeInvalid()
    expect(input).toHaveAccessibleDescription(expect.stringContaining(HELP))
    expect(screen.getByText('Use a registered name.')).toBeVisible()
    expect(screen.getByText('Choose an available target.')).toBeVisible()
    expect(screen.getAllByRole('button')).toHaveLength(1)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('should show help on the term and keep it available while the pointer is over the help', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.hover(screen.getByText('Objective target'))
    const tooltip = await screen.findByRole('tooltip')
    expect(tooltip).toHaveTextContent(HELP)
    await user.hover(tooltip)
    expect(tooltip).toBeVisible()
    await user.unhover(tooltip)
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('should support keyboard focus and Escape without trapping focus in the help', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.tab()
    expect(screen.getByText('Objective target')).toHaveFocus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent(HELP)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
    expect(screen.getByText('Objective target')).toHaveFocus()
    await user.tab()
    expect(screen.getByRole('textbox', { name: 'Objective target' })).toHaveFocus()
  })

  it('should keep normal label clicks focused on the field without pinning desktop help', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.click(screen.getByText('Objective target'))
    expect(screen.getByRole('textbox', { name: 'Objective target' })).toHaveFocus()
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('should let touch users open help and dismiss it by tapping the field', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.pointer([
      { keys: '[TouchA>]', target: screen.getByText('Objective target') },
      { keys: '[/TouchA]' },
    ])
    expect(await screen.findByRole('tooltip')).toHaveTextContent(HELP)
    await user.click(screen.getByRole('textbox', { name: 'Objective target' }))
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('should leave fields without help as normal fields', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><HelpField label="Name"><Input /></HelpField></FluentProvider>)

    await user.tab()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
