import { render, screen, fireEvent } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import HistoryFiltersBar from './HistoryFiltersBar'
import { DEFAULT_HISTORY_FILTERS } from './historyFilters'

jest.mock('./AttackHistory.styles', () => ({
  useAttackHistoryStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('HistoryFiltersBar', () => {
  const defaultProps = {
    filters: { ...DEFAULT_HISTORY_FILTERS },
    onFiltersChange: jest.fn(),
    attackClassOptions: [] as string[],
    converterOptions: [] as string[],
    operatorOptions: [] as string[],
    operationOptions: [] as string[],
    otherLabelOptions: [] as string[],
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render all filter dropdowns', () => {
    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.getByTestId('attack-class-filter')).toBeInTheDocument()
    expect(screen.getByTestId('outcome-filter')).toBeInTheDocument()
    expect(screen.getByTestId('converter-filter')).toBeInTheDocument()
    expect(screen.getByTestId('operator-filter')).toBeInTheDocument()
    expect(screen.getByTestId('operation-filter')).toBeInTheDocument()
    expect(screen.getByTestId('label-filter')).toBeInTheDocument()
  })

  it('should not show reset button when no filters are active', () => {
    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('reset-filters-btn')).not.toBeInTheDocument()
  })

  it('should show reset button when a filter is active', () => {
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, outcome: 'success' }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} filters={activeFilters} />
      </TestWrapper>
    )

    expect(screen.getByTestId('reset-filters-btn')).toBeInTheDocument()
  })

  it('should call onFiltersChange with defaults when reset is clicked', () => {
    const onFiltersChange = jest.fn()
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, outcome: 'success', operator: 'alice' }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} filters={activeFilters} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('reset-filters-btn'))
    expect(onFiltersChange).toHaveBeenCalledWith(DEFAULT_HISTORY_FILTERS)
  })

  it('should call onFiltersChange when attack class filter is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      attackClassOptions: ['CrescendoAttack', 'ManualAttack'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('attack-class-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('CrescendoAttack')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ attackClass: 'CrescendoAttack' })
    )
  })

  it('should call onFiltersChange when outcome filter is selected', async () => {
    const onFiltersChange = jest.fn()

    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('outcome-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('Failure')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ outcome: 'failure' })
    )
  })

  it('should call onFiltersChange when converter filter is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      converterOptions: ['Base64Converter', 'ROT13Converter'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('converter-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('ROT13Converter')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ converter: 'ROT13Converter' })
    )
  })

  it('should call onFiltersChange when operator filter is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      operatorOptions: ['alice', 'bob'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('operator-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('bob')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ operator: 'bob' })
    )
  })

  it('should call onFiltersChange when operation filter is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      operationOptions: ['op_alpha', 'op_beta'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('operation-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('op_beta')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ operation: 'op_beta' })
    )
  })

  it('should update label search text when typing in label combobox', () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      otherLabelOptions: ['team:red', 'team:blue', 'env:prod'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const inputs = screen.getAllByRole('combobox')
    const labelInput = inputs[inputs.length - 1]
    fireEvent.change(labelInput, { target: { value: 'team' } })

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ labelSearchText: 'team' })
    )
  })

  it('should call onFiltersChange when a label option is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      otherLabelOptions: ['team:red', 'team:blue'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const labelCombobox = screen.getByTestId('label-filter')
    fireEvent.click(labelCombobox)

    const option = await screen.findByText('team:red')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ otherLabels: expect.any(Array), labelSearchText: '' })
    )
  })

  it('should show reset button when otherLabels are active', () => {
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, otherLabels: ['team:red'] }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} filters={activeFilters} />
      </TestWrapper>
    )

    expect(screen.getByTestId('reset-filters-btn')).toBeInTheDocument()
  })
})
