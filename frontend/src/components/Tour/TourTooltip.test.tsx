import { render, screen } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import TourTooltip from './TourTooltip'

// Minimal props that Joyride passes to a custom tooltipComponent.
// We only populate the fields TourTooltip actually reads.
function makeProps(overrides: Record<string, unknown> = {}) {
  const noop = jest.fn()
  const buttonProps = {
    'aria-label': 'test',
    'data-action': 'test',
    onClick: noop,
    role: 'button',
    title: 'test',
  }

  return {
    continuous: true,
    index: 0,
    isLastStep: false,
    size: 5,
    step: {
      content: 'Step content text',
      target: 'body',
      disableBeacon: true,
    },
    backProps: { ...buttonProps, 'aria-label': 'Back', title: 'Back' },
    primaryProps: { ...buttonProps, 'aria-label': 'Next', title: 'Next' },
    skipProps: { ...buttonProps, 'aria-label': 'Skip tour', title: 'Skip tour' },
    closeProps: { ...buttonProps, 'aria-label': 'Close', title: 'Close' },
    tooltipProps: { role: 'alertdialog' as const },
    ...overrides,
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  } as any
}

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('TourTooltip', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders step content text', () => {
    render(<TestWrapper><TourTooltip {...makeProps()} /></TestWrapper>)

    expect(screen.getByText('Step content text')).toBeInTheDocument()
  })

  it('renders step counter', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ index: 2, size: 5 })} /></TestWrapper>)

    expect(screen.getByText('3 of 5')).toBeInTheDocument()
  })

  it('renders Next button on non-last steps', () => {
    render(<TestWrapper><TourTooltip {...makeProps()} /></TestWrapper>)

    expect(screen.getByRole('button', { name: /next/i })).toBeInTheDocument()
  })

  it('renders Finish button on last step', () => {
    const props = makeProps({
      isLastStep: true,
      index: 4,
      primaryProps: {
        'aria-label': "Anchors Away!",
        'data-action': 'primary',
        onClick: jest.fn(),
        role: 'button',
        title: "Anchors Away!",
      },
    })
    render(<TestWrapper><TourTooltip {...props} /></TestWrapper>)

    expect(screen.getByRole('button', { name: /anchors away/i })).toBeInTheDocument()
    expect(screen.queryByText('Next')).not.toBeInTheDocument()
  })

  it('hides Back button on first step', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ index: 0 })} /></TestWrapper>)

    expect(screen.queryByRole('button', { name: /back/i })).not.toBeInTheDocument()
  })

  it('shows Back button after first step', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ index: 1 })} /></TestWrapper>)

    expect(screen.getByRole('button', { name: /back/i })).toBeInTheDocument()
  })

  it('shows Skip tour button on non-last steps', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ isLastStep: false })} /></TestWrapper>)

    expect(screen.getByRole('button', { name: /skip tour/i })).toBeInTheDocument()
  })

  it('hides Skip tour button on last step', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ isLastStep: true })} /></TestWrapper>)

    expect(screen.queryByRole('button', { name: /skip tour/i })).not.toBeInTheDocument()
  })

  it('hides close (X) button on last step', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ isLastStep: true })} /></TestWrapper>)

    // Close button uses DismissRegular icon and has aria-label "Close"
    expect(screen.queryByRole('button', { name: /close/i })).not.toBeInTheDocument()
  })

  it('shows close (X) button on non-last steps', () => {
    render(<TestWrapper><TourTooltip {...makeProps({ isLastStep: false })} /></TestWrapper>)

    expect(screen.getByRole('button', { name: /close/i })).toBeInTheDocument()
  })

  it('renders alertdialog role from tooltipProps', () => {
    render(<TestWrapper><TourTooltip {...makeProps()} /></TestWrapper>)

    expect(screen.getByRole('alertdialog')).toBeInTheDocument()
  })

  it('uses light theme when isDarkMode is false', () => {
    render(<TestWrapper><TourTooltip {...makeProps()} isDarkMode={false} /></TestWrapper>)

    // Verify component renders without error in light mode
    expect(screen.getByText('Step content text')).toBeInTheDocument()
  })
})
