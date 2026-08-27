import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { CustomInitializer } from '@/types'

import CustomInitializers from './CustomInitializers'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const ITEMS: CustomInitializer[] = [
  {
    initializer_name: 'custom_target',
    script_content: 'def initialize():\n    pass',
    source: 'https://account.blob.core.windows.net/copyrit/custom-initializers/custom_target.py',
  },
  {
    initializer_name: 'second_target',
    script_content: 'def initialize_second():\n    pass',
    source: 'https://account.blob.core.windows.net/copyrit/custom-initializers/second_target.py',
  },
]

describe('CustomInitializers', () => {
  const defaultProps = {
    items: ITEMS,
    registering: false,
    deletingName: null,
    onRegister: jest.fn(),
    onDelete: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should require confirmation before deleting stored source', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <CustomInitializers {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Remove' }))

    expect(defaultProps.onDelete).not.toHaveBeenCalled()
    const dialog = screen.getByRole('dialog', { name: 'Remove custom initializer' })

    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    expect(defaultProps.onDelete).toHaveBeenCalledWith('custom_target')
    expect(screen.getByRole('button', { name: 'second_target' })).toHaveAttribute('aria-current', 'page')
  })

  it('should display stored source as read-only', () => {
    render(
      <TestWrapper>
        <CustomInitializers {...defaultProps} />
      </TestWrapper>,
    )

    const editor = screen.getByLabelText('Python source')
    expect(editor).toBeDisabled()
    expect(editor).toHaveValue('def initialize():\n    pass')
    expect(screen.getByText(ITEMS[0].source, { selector: 'label' })).toBeInTheDocument()
  })

  it('should select a custom initializer from the file navigation', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <CustomInitializers {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'second_target' }))

    expect(screen.getByLabelText('Python source')).toHaveValue('def initialize_second():\n    pass')
  expect(screen.getByText(ITEMS[1].source, { selector: 'label' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'second_target' })).toHaveAttribute('aria-current', 'page')
  })

  it('should add a custom initializer script', async () => {
    const user = userEvent.setup()
    defaultProps.onRegister.mockResolvedValue(true)
    render(
      <TestWrapper>
        <CustomInitializers {...defaultProps} />
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Add initializer' }))
    const dialog = screen.getByRole('dialog', { name: 'Add custom initializer' })
    await user.type(within(dialog).getByRole('textbox', { name: /Initializer name/ }), 'new_custom')
    await user.type(within(dialog).getByRole('textbox', { name: 'Python source' }), 'class NewCustom: pass')
    await user.click(within(dialog).getByRole('button', { name: 'Add' }))

    expect(defaultProps.onRegister).toHaveBeenCalledWith('new_custom', 'class NewCustom: pass')
  })
})
