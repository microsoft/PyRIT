import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import SystemPromptBanner from './SystemPromptBanner'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

// jsdom has no layout engine, so scrollWidth/clientWidth are 0 by default (no overflow).
// Force overflow by overriding the prototype getters for the duration of a test.
function mockOverflow(scrollWidth: number, clientWidth: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => scrollWidth })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => clientWidth })
}

describe('SystemPromptBanner', () => {
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
  })

  it('renders the label and the system prompt content', () => {
    render(
      <TestWrapper>
        <SystemPromptBanner content="You are a pirate." />
      </TestWrapper>
    )

    expect(screen.getByText('System Prompt')).toBeInTheDocument()
    expect(screen.getByText('You are a pirate.')).toBeInTheDocument()
  })

  it('does not render an expand toggle when the content fits on one line', () => {
    render(
      <TestWrapper>
        <SystemPromptBanner content="Be terse." />
      </TestWrapper>
    )

    expect(screen.queryByRole('button', { name: /system prompt/i })).not.toBeInTheDocument()
  })

  it('renders a collapsed expand toggle when the content overflows', () => {
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <SystemPromptBanner content="A very long system prompt that does not fit on one line." />
      </TestWrapper>
    )

    expect(screen.getByRole('button', { name: /system prompt/i })).toHaveAttribute('aria-expanded', 'false')
  })

  it('expands when the overflowing header is clicked', async () => {
    const user = userEvent.setup()
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <SystemPromptBanner content="A very long system prompt that does not fit on one line." />
      </TestWrapper>
    )

    const toggle = screen.getByRole('button', { name: /system prompt/i })
    await user.click(toggle)

    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
