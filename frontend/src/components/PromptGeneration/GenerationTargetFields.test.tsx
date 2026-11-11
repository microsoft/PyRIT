import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { ASSERT_DESCRIPTOR } from '@/test-utils/promptGenerationFixtures'
import { makeTarget } from '@/test-utils/targetFixtures'

import GenerationTargetFields from './GenerationTargetFields'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('GenerationTargetFields', () => {
  const defaultProps = {
    roles: ASSERT_DESCRIPTOR.target_roles,
    targets: [makeTarget({ target_registry_name: 'gpt', model_name: 'gpt-4o' })],
    loading: false,
    error: null,
    value: {},
    onChange: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('shows a picker per role and reports the chosen targets', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><GenerationTargetFields {...defaultProps} value={{ research: 'gpt' }} /></TestWrapper>)

    const generation = screen.getByTestId('generation-target-generation')
    expect(within(generation).getAllByRole('option').map((option) => option.textContent))
      .toEqual(['Select a target', 'gpt (gpt-4o)'])
    await user.selectOptions(generation, 'gpt')
    expect(defaultProps.onChange).toHaveBeenLastCalledWith({ research: 'gpt', generation: 'gpt' })
    await user.selectOptions(screen.getByTestId('generation-target-research'), '')
    expect(defaultProps.onChange).toHaveBeenLastCalledWith({})
  })

  it('flags a saved target that is no longer registered', () => {
    render(<TestWrapper><GenerationTargetFields {...defaultProps} value={{ generation: 'retired' }} /></TestWrapper>)

    expect(screen.getByText('"retired" is not a registered target.')).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'retired' })).toBeDisabled()
  })

  it('shows loading and registry errors', () => {
    const { rerender } = render(<TestWrapper><GenerationTargetFields {...defaultProps} loading /></TestWrapper>)
    expect(screen.getByText('Loading targets...')).toBeInTheDocument()

    rerender(<TestWrapper><GenerationTargetFields {...defaultProps} targets={[]} error="Registry offline" /></TestWrapper>)
    expect(screen.getAllByText('Registry offline')).toHaveLength(2)
  })
})
