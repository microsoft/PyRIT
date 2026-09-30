import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import TargetFiltersBar from './TargetFiltersBar'
import { DEFAULT_TARGET_FILTERS, type TargetFilterOptions } from './targetFilters'

jest.mock('./TargetFiltersBar.styles', () => ({
  useTargetFiltersBarStyles: () => new Proxy({}, { get: () => '' }),
}))

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const OPTIONS: TargetFilterOptions = {
  types: [{ value: 'OpenAIChatTarget', label: 'OpenAIChatTarget' }, { value: 'OpenAITTSTarget', label: 'OpenAITTSTarget' }],
  inputs: [{ value: 'text', label: 'Text' }, { value: 'image_path', label: 'Image' }],
  outputs: [],
  capabilities: [{ value: 'supports_json_schema', label: 'JSON Schema' }],
}

describe('TargetFiltersBar', () => {
  const defaultProps = {
    filters: { ...DEFAULT_TARGET_FILTERS },
    options: OPTIONS,
    onFiltersChange: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render reset first, then only the filters that have choices', () => {
    render(<TestWrapper><TargetFiltersBar {...defaultProps} /></TestWrapper>)

    const controls = screen.getAllByRole('combobox').map((combobox) => combobox.getAttribute('aria-label'))
    expect(controls).toEqual(['Filter by type:', 'Filter by input:', 'Filter by capability:'])
    const reset = screen.getByRole('button', { name: 'Reset all filters' })
    expect(reset.compareDocumentPosition(screen.getByRole('combobox', { name: 'Filter by type:' })))
      .toBe(Node.DOCUMENT_POSITION_FOLLOWING)
  })

  it('should render nothing when no filter has choices', () => {
    const options = { types: [], inputs: [], outputs: [], capabilities: [] }
    render(<TestWrapper><TargetFiltersBar {...defaultProps} options={options} /></TestWrapper>)

    expect(screen.queryByTestId('target-filters')).not.toBeInTheDocument()
  })

  it('should name the filter and count extra selections', () => {
    render(
      <TestWrapper>
        <TargetFiltersBar {...defaultProps} filters={{ ...DEFAULT_TARGET_FILTERS, inputs: ['image_path', 'text'] }} />
      </TestWrapper>,
    )

    expect(screen.getByRole('combobox', { name: 'Filter by input:' })).toHaveValue('Inputs: Image (+1)')
    expect(screen.getByRole('combobox', { name: 'Filter by type:' })).toHaveValue('')
  })

  it('should add a checked choice to the filter', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetFiltersBar {...defaultProps} filters={{ ...DEFAULT_TARGET_FILTERS, inputs: ['text'] }} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('combobox', { name: 'Filter by input:' }))
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Image' }))

    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({ ...DEFAULT_TARGET_FILTERS, inputs: ['text', 'image_path'] })
  })

  it('should narrow the choices to what you type and check the match with Enter', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <TargetFiltersBar {...defaultProps} filters={{ ...DEFAULT_TARGET_FILTERS, inputs: ['text'] }} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('combobox', { name: 'Filter by input:' }))
    await user.keyboard('ima')

    const choices = await screen.findAllByRole('menuitemcheckbox')
    expect(choices.map((choice: HTMLElement) => choice.textContent)).toEqual(['Image'])
    await user.keyboard('{Enter}')
    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith({ ...DEFAULT_TARGET_FILTERS, inputs: ['text', 'image_path'] })
  })

  it('should reset every filter, and only when one is set', async () => {
    const user = userEvent.setup()
    const { rerender } = render(<TestWrapper><TargetFiltersBar {...defaultProps} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Reset all filters' })).toBeDisabled()

    rerender(
      <TestWrapper>
        <TargetFiltersBar {...defaultProps} filters={{ ...DEFAULT_TARGET_FILTERS, capabilities: ['supports_json_schema'] }} />
      </TestWrapper>,
    )
    await user.click(screen.getByRole('button', { name: 'Reset all filters' }))

    expect(defaultProps.onFiltersChange).toHaveBeenCalledWith(DEFAULT_TARGET_FILTERS)
  })
})
