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
    attackTypeOptions: [] as string[],
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

    expect(screen.getByTestId('attack-type-filter')).toBeInTheDocument()
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
    const activeFilters = { ...DEFAULT_HISTORY_FILTERS, outcome: 'success', operator: ['alice'] }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...defaultProps} filters={activeFilters} onFiltersChange={onFiltersChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('reset-filters-btn'))
    expect(onFiltersChange).toHaveBeenCalledWith(DEFAULT_HISTORY_FILTERS)
  })

  it('should call onFiltersChange when attack type filter is selected', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      attackTypeOptions: ['CrescendoAttack', 'ManualAttack'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const dropdown = screen.getByTestId('attack-type-filter')
    fireEvent.click(dropdown)

    const option = await screen.findByText('CrescendoAttack')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ attackTypes: ['CrescendoAttack'] })
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
      expect.objectContaining({ converter: ['ROT13Converter'] })
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
      expect.objectContaining({ operator: ['bob'] })
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
      expect.objectContaining({ operation: ['op_beta'] })
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

  it('should set hasConverters=false and clear converter list when "(No converters)" sentinel is selected', async () => {
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

    fireEvent.click(screen.getByTestId('converter-filter'))
    const option = await screen.findByText('(No converters)')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ converter: [], hasConverters: false })
    )
  })

  it('should replace sentinel with real converter when user picks a converter while sentinel is active', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      converterOptions: ['Base64Converter', 'ROT13Converter'],
      filters: { ...DEFAULT_HISTORY_FILTERS, hasConverters: false as boolean | undefined },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('converter-filter'))
    const option = await screen.findByText('ROT13Converter')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ converter: ['ROT13Converter'], hasConverters: undefined })
    )
  })

  it('should replace real converters with sentinel when user picks sentinel while real converters are active', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      converterOptions: ['Base64Converter', 'ROT13Converter'],
      filters: { ...DEFAULT_HISTORY_FILTERS, converter: ['Base64Converter'] },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('converter-filter'))
    const option = await screen.findByText('(No converters)')
    fireEvent.click(option)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ converter: [], hasConverters: false })
    )
  })

  it('should not render the match-mode toggle when fewer than two converters are selected', () => {
    const props = {
      ...defaultProps,
      converterOptions: ['Base64Converter', 'ROT13Converter'],
      filters: { ...DEFAULT_HISTORY_FILTERS, converter: ['Base64Converter'] },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('converter-match-mode-toggle')).not.toBeInTheDocument()
  })

  it('should render the match-mode toggle and emit "all" when flipped with two converters selected', () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      converterOptions: ['Base64Converter', 'ROT13Converter'],
      filters: { ...DEFAULT_HISTORY_FILTERS, converter: ['Base64Converter', 'ROT13Converter'] },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const toggle = screen.getByTestId('converter-match-mode-toggle')
    expect(toggle).toBeInTheDocument()

    fireEvent.click(toggle)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ converterMatchMode: 'all' })
    )
  })

  it('should display "name (+N)" when multiple values are selected in a multi-select Combobox', () => {
    const props = {
      ...defaultProps,
      operatorOptions: ['alice', 'bob', 'carol'],
      filters: { ...DEFAULT_HISTORY_FILTERS, operator: ['alice', 'bob', 'carol'] },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const input = screen.getByTestId('operator-filter') as HTMLInputElement
    expect(input.value).toBe('alice (+2)')
  })

  it('should display just the name when exactly one value is selected in a multi-select Combobox', () => {
    const props = {
      ...defaultProps,
      operatorOptions: ['alice', 'bob'],
      filters: { ...DEFAULT_HISTORY_FILTERS, operator: ['alice'] },
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const input = screen.getByTestId('operator-filter') as HTMLInputElement
    expect(input.value).toBe('alice')
  })

  it('should filter multi-select Combobox options by typed search text and reset search on selection', async () => {
    const onFiltersChange = jest.fn()
    const props = {
      ...defaultProps,
      onFiltersChange,
      operatorOptions: ['alice', 'bob', 'carol'],
    }

    render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const operatorInput = screen.getByTestId('operator-filter') as HTMLInputElement
    // Open and type a search; only matching options should remain visible.
    fireEvent.click(operatorInput)
    fireEvent.change(operatorInput, { target: { value: 'car' } })

    expect(operatorInput.value).toBe('car')
    expect(screen.queryByText('alice')).not.toBeInTheDocument()
    expect(screen.queryByText('bob')).not.toBeInTheDocument()
    const match = await screen.findByText('carol')
    fireEvent.click(match)

    expect(onFiltersChange).toHaveBeenCalledWith(
      expect.objectContaining({ operator: ['carol'] })
    )
  })

  it('should keep the outcome Dropdown display label after a sibling filter changes', () => {
    const props = {
      ...defaultProps,
      attackTypeOptions: ['PromptSendingAttack'],
      filters: { ...DEFAULT_HISTORY_FILTERS, outcome: 'success' },
    }

    const { rerender } = render(
      <TestWrapper>
        <HistoryFiltersBar {...props} />
      </TestWrapper>
    )

    const outcomeInput = screen.getByTestId('outcome-filter') as HTMLInputElement
    expect(outcomeInput.value).toBe('Success')

    rerender(
      <TestWrapper>
        <HistoryFiltersBar
          {...props}
          filters={{ ...props.filters, attackTypes: ['PromptSendingAttack'] }}
        />
      </TestWrapper>
    )

    expect((screen.getByTestId('outcome-filter') as HTMLInputElement).value).toBe('Success')
  })
})
