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
  })
})