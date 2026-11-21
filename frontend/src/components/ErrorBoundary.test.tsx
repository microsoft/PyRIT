import { render, screen, fireEvent } from '@testing-library/react'
import { ErrorBoundary } from './ErrorBoundary'

// Component that throws on render
function ThrowingChild({ shouldThrow }: { shouldThrow: boolean }) {
  if (shouldThrow) {
    throw new Error('Test crash')
  }
  return <div data-testid="child-content">OK</div>
}

describe('ErrorBoundary', () => {
  let consoleSpy: jest.SpyInstance

  beforeEach(() => {
    consoleSpy = jest.spyOn(console, 'error').mockImplementation(() => {})
  })

  afterEach(() => {
    consoleSpy.mockRestore()
  })

  it('renders children when no error occurs', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow={false} />
      </ErrorBoundary>
    )

    expect(screen.getByTestId('child-content')).toBeInTheDocument()
    expect(screen.queryByTestId('error-boundary-fallback')).toBeNull()
  })

  it('catches render error and shows fallback', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(screen.getByTestId('error-boundary-fallback')).toBeInTheDocument()
    expect(screen.getByText(/test crash/i)).toBeInTheDocument()
    expect(screen.getByText('Try again')).toBeInTheDocument()
  })

  it('"Try again" resets error state and re-renders children', () => {
    let shouldThrow = true

    function ToggleChild() {
      if (shouldThrow) { throw new Error('First crash') }
      return <div data-testid="child-content">OK</div>
    }

    render(
      <ErrorBoundary>
        <ToggleChild />
      </ErrorBoundary>
    )

    expect(screen.getByTestId('error-boundary-fallback')).toBeInTheDocument()

    shouldThrow = false
    fireEvent.click(screen.getByText('Try again'))

    expect(screen.getByTestId('child-content')).toBeInTheDocument()
    expect(screen.queryByTestId('error-boundary-fallback')).toBeNull()
  })

  it('offers a reload instead of a retry when a screen chunk failed to load', () => {
    function FailedChunk(): never {
      throw new TypeError('Failed to fetch dynamically imported module: http://x/assets/A.js')
    }
    render(
      <ErrorBoundary>
        <FailedChunk />
      </ErrorBoundary>
    )

    expect(screen.getByRole('button', { name: 'Reload page' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull()
  })

  it('logs error to console via onError', () => {
    render(
      <ErrorBoundary>
        <ThrowingChild shouldThrow={true} />
      </ErrorBoundary>
    )

    expect(consoleSpy).toHaveBeenCalledWith(
      '[ErrorBoundary] Caught render error:',
      expect.any(Error),
      expect.any(String)
    )
  })
})
