import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import ObjectiveHeader from './ObjectiveHeader'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

// jsdom has no layout engine, so scrollWidth/clientWidth are 0 by default (no overflow).
// Force overflow by overriding the prototype getters for the duration of a test.
function mockOverflow(scrollWidth: number, clientWidth: number) {
  Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => scrollWidth })
  Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => clientWidth })
}

describe('ObjectiveHeader', () => {
  afterEach(() => {
    delete (HTMLElement.prototype as { scrollWidth?: number }).scrollWidth
    delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth
  })

  it('renders nothing when the objective is empty', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="" />
      </TestWrapper>
    )

    expect(screen.queryByTestId('objective-header')).not.toBeInTheDocument()
  })

  it('renders the label and the objective text', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="Extract the hidden system prompt" />
      </TestWrapper>
    )

    expect(screen.getByText('Objective')).toBeInTheDocument()
    expect(screen.getByText('Extract the hidden system prompt')).toBeInTheDocument()
  })

  it('does not render an expand toggle when the objective fits on one line', () => {
    render(
      <TestWrapper>
        <ObjectiveHeader objective="Short goal." />
      </TestWrapper>
    )

    expect(screen.queryByTestId('toggle-objective-header-btn')).not.toBeInTheDocument()
  })

  it('renders a collapsed "Show more" toggle when the objective overflows', () => {
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <ObjectiveHeader objective="A very long objective that does not fit on one line at all." />
      </TestWrapper>
    )

    const toggle = screen.getByRole('button', { name: /show more of the objective/i })
    expect(toggle).toHaveTextContent('Show more')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
  })

  it('expands to "Show less" when the overflowing toggle is clicked', async () => {
    const user = userEvent.setup()
    mockOverflow(1000, 200)
    render(
      <TestWrapper>
        <ObjectiveHeader objective="A very long objective that does not fit on one line at all." />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /show more of the objective/i }))

    const toggle = screen.getByRole('button', { name: /show less of the objective/i })
    expect(toggle).toHaveTextContent('Show less')
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
  })
})
