import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, FluentProvider, Input, Popover, PopoverSurface, PopoverTrigger, webLightTheme } from '@fluentui/react-components'

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

  it('should retain the label, description, required state, and visible validation with a named information button', () => {
    render(<ExampleField />)

    const input = screen.getByRole('textbox', { name: 'Objective target' })
    expect(input).toBeRequired()
    expect(input).toBeInvalid()
    expect(input).toHaveAccessibleDescription(expect.stringContaining(HELP))
    expect(screen.getByText('Use a registered name.')).toBeVisible()
    expect(screen.getByText('Choose an available target.')).toBeVisible()
    expect(screen.getByRole('button', { name: /Objective target.*information/i })).toBeEnabled()
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
    const information = screen.getByRole('button', { name: /Objective target.*information/i })
    expect(information).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('note')).toHaveTextContent(HELP)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('note')).not.toBeInTheDocument())
    await waitFor(() => expect(information).toHaveFocus())
    await user.tab()
    expect(screen.getByRole('textbox', { name: 'Objective target' })).toHaveFocus()
  })

  it('should keep normal label clicks focused on the field without pinning desktop help', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.click(screen.getByText('Objective target'))
    expect(screen.getByRole('textbox', { name: 'Objective target' })).toHaveFocus()
    await user.hover(screen.getByRole('textbox', { name: 'Objective target' }))
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('should let touch users open the native information note and dismiss it by tapping the field', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.pointer([
      { keys: '[TouchA>]', target: screen.getByRole('button', { name: /Objective target.*information/i }) },
      { keys: '[/TouchA]' },
    ])
    expect(await screen.findByRole('note')).toHaveTextContent(HELP)
    await user.click(screen.getByRole('textbox', { name: 'Objective target' }))
    await waitFor(() => expect(screen.queryByRole('note')).not.toBeInTheDocument())
  })

  it('should not show duplicate hover and information disclosures at the same time', async () => {
    const user = userEvent.setup()
    render(<ExampleField />)

    await user.hover(screen.getByText('Objective target'))
    expect(await screen.findByRole('tooltip')).toHaveTextContent(HELP)
    await user.click(screen.getByRole('button', { name: /Objective target.*information/i }))
    expect(await screen.findByRole('note')).toHaveTextContent(HELP)
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('should leave fields without help as normal fields', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><HelpField label="Name"><Input /></HelpField></FluentProvider>)

    await user.tab()
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('should dismiss the information note without closing an enclosing editor', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}>
      <Popover defaultOpen>
        <PopoverTrigger disableButtonEnhancement><Button>Options</Button></PopoverTrigger>
        <PopoverSurface aria-label="Editing options">
          <HelpField label="Objective target" help={HELP}><Input /></HelpField>
        </PopoverSurface>
      </Popover>
    </FluentProvider>)

    await user.click(screen.getByRole('button', { name: /Objective target.*information/i }))
    expect(await screen.findByRole('note')).toHaveTextContent(HELP)
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('note')).not.toBeInTheDocument())
    expect(screen.getByRole('group', { name: 'Editing options' })).toBeVisible()
  })
})
