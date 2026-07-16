import { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import SystemPromptSetup from './SystemPromptSetup'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

/** Stateful harness mirroring how ChatWindow owns the systemPrompt value. */
function Harness({
  initial = '',
  disabled = false,
}: {
  initial?: string
  disabled?: boolean
}) {
  const [value, setValue] = useState(initial)
  return (
    <TestWrapper>
      <SystemPromptSetup
        value={value}
        onChange={setValue}
        disabled={disabled}
      />
    </TestWrapper>
  )
}

describe('SystemPromptSetup', () => {
  it('renders collapsed by default with no textarea visible', () => {
    render(<Harness />)
    expect(screen.getByRole('button', { name: /system prompt/i })).toBeInTheDocument()
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
  })

  it('expands to reveal the textarea when the toggle is clicked', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: /system prompt/i }))

    expect(screen.getByRole('textbox', { name: /system prompt/i })).toBeInTheDocument()
  })

  it('reflects typed text and updates the character counter', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    await user.type(screen.getByRole('textbox', { name: /system prompt/i }), 'hello')

    expect(screen.getByRole('textbox', { name: /system prompt/i })).toHaveValue('hello')
    expect(screen.getByText(/5 characters/i)).toBeInTheDocument()
  })

  it('calls onChange with the new value as the user types', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <SystemPromptSetup value="" onChange={onChange} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /system prompt/i }))
    await user.type(screen.getByRole('textbox', { name: /system prompt/i }), 'H')

    expect(onChange).toHaveBeenCalledWith('H')
  })

  it('flags the counter when the value exceeds the soft limit', async () => {
    const user = userEvent.setup()
    render(<Harness initial={'x'.repeat(2001)} />)

    await user.click(screen.getByRole('button', { name: /system prompt/i }))

    expect(screen.getByTestId('system-prompt-counter')).toHaveTextContent('2001 characters')
  })

  describe('when the target does not support system prompts', () => {
    const disabledReason = 'This target does not support system prompts.'

    it('disables the toggle and shows the reason', () => {
      render(<Harness disabled />)

      expect(screen.getByRole('button', { name: /system prompt/i })).toBeDisabled()
      expect(screen.getByText(disabledReason)).toBeInTheDocument()
    })

    it('does not expand when the disabled toggle is clicked', async () => {
      const user = userEvent.setup()
      render(<Harness disabled />)

      await user.click(screen.getByRole('button', { name: /system prompt/i }))

      expect(screen.queryByRole('textbox')).not.toBeInTheDocument()
    })
  })
})
