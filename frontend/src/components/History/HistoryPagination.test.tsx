import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import HistoryPagination from './HistoryPagination'

jest.mock('./AttackHistory.styles', () => ({
  useAttackHistoryStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('HistoryPagination', () => {
  const defaultProps = {
    page: 0,
    isLastPage: false,
    onPrevPage: jest.fn(),
    onNextPage: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render page number (1-indexed)', () => {
    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} page={2} />
      </TestWrapper>
    )

    expect(screen.getByText('Page 3')).toBeInTheDocument()
  })

  it('should disable "First" button on page 0', () => {
    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} page={0} />
      </TestWrapper>
    )

    const firstBtn = screen.getByTestId('prev-page-btn')
    expect(firstBtn).toBeDisabled()
  })

  it('should disable "Next" button when isLastPage is true', () => {
    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} isLastPage={true} />
      </TestWrapper>
    )

    const nextBtn = screen.getByTestId('next-page-btn')
    expect(nextBtn).toBeDisabled()
  })

  it('should call onPrevPage when "First" is clicked', () => {
    const onPrevPage = jest.fn()

    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} page={3} onPrevPage={onPrevPage} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('prev-page-btn'))
    expect(onPrevPage).toHaveBeenCalledTimes(1)
  })

  it('should call onNextPage when "Next" is clicked', () => {
    const onNextPage = jest.fn()

    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} onNextPage={onNextPage} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('next-page-btn'))
    expect(onNextPage).toHaveBeenCalledTimes(1)
  })

  it('should enable both buttons on a middle page', () => {
    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} page={2} isLastPage={false} />
      </TestWrapper>
    )

    expect(screen.getByTestId('prev-page-btn')).not.toBeDisabled()
    expect(screen.getByTestId('next-page-btn')).not.toBeDisabled()
  })

  it('should render "First" and "Next" button labels', () => {
    render(
      <TestWrapper>
        <HistoryPagination {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByText('First')).toBeInTheDocument()
    expect(screen.getByText('Next')).toBeInTheDocument()
  })
})
