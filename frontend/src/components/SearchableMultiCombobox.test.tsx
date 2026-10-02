import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import type { FilterOption } from '@/types'
import SearchableMultiCombobox from './SearchableMultiCombobox'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const OPTIONS: FilterOption[] = [
  { value: 'text', label: 'Text' },
  { value: 'image_path', label: 'Image' },
  { value: 'audio_path', label: 'Audio' },
]

describe('SearchableMultiCombobox', () => {
  const defaultProps = {
    options: OPTIONS,
    selectedOptions: [] as string[],
    onSelect: jest.fn(),
    placeholder: 'All inputs',
    testId: 'input-filter',
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should show nothing but the placeholder when no choice is checked', () => {
    render(<TestWrapper><SearchableMultiCombobox {...defaultProps} ariaLabel="Filter by input:" /></TestWrapper>)

    const combobox = screen.getByRole('combobox', { name: 'Filter by input:' })
    expect(combobox).toHaveValue('')
    expect(combobox).toHaveAttribute('placeholder', 'All inputs')
  })

  it('should summarize the checked choices by label, with an optional prefix', () => {
    const { rerender } = render(
      <TestWrapper><SearchableMultiCombobox {...defaultProps} selectedOptions={['image_path', 'text']} /></TestWrapper>,
    )
    expect(screen.getByTestId('input-filter')).toHaveValue('Image (+1)')

    rerender(
      <TestWrapper>
        <SearchableMultiCombobox {...defaultProps} selectedOptions={['image_path']} summaryPrefix="Inputs" />
      </TestWrapper>,
    )
    expect(screen.getByTestId('input-filter')).toHaveValue('Inputs: Image')
  })

  it('should fall back to the value when a checked choice is not among the options', () => {
    render(<TestWrapper><SearchableMultiCombobox {...defaultProps} selectedOptions={['video_path']} /></TestWrapper>)

    expect(screen.getByTestId('input-filter')).toHaveValue('video_path')
  })

  it('should narrow the choices by label while typing, ignoring case and outer spaces', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SearchableMultiCombobox {...defaultProps} /></TestWrapper>)

    await user.click(screen.getByTestId('input-filter'))
    await user.keyboard(' IMA ')

    const choices = await screen.findAllByRole('menuitemcheckbox')
    expect(choices.map((choice: HTMLElement) => choice.textContent)).toEqual(['Image'])
    await user.clear(screen.getByTestId('input-filter'))
    await user.keyboard('path')
    expect(screen.queryAllByRole('menuitemcheckbox')).toHaveLength(0)
  })

  it('should keep checked choices the search hides and clear the search after a pick', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><SearchableMultiCombobox {...defaultProps} selectedOptions={['text']} /></TestWrapper>)

    const combobox = screen.getByTestId('input-filter')
    await user.click(combobox)
    await user.keyboard('aud')
    await user.click(await screen.findByRole('menuitemcheckbox', { name: 'Audio' }))

    expect(defaultProps.onSelect).toHaveBeenCalledWith(['text', 'audio_path'])
    expect(combobox).toHaveValue('')
  })
})
