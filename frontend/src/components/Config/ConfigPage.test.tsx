import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ConfigPage from './ConfigPage'

jest.mock('./TargetConfig', () => {
  const MockTargetConfig = () => <div data-testid="target-config-panel" />
  MockTargetConfig.displayName = 'MockTargetConfig'
  return {
    __esModule: true,
    default: MockTargetConfig,
  }
})

jest.mock('./InitializerConfig', () => {
  const MockInitializerConfig = () => <div data-testid="initializer-config-panel" />
  MockInitializerConfig.displayName = 'MockInitializerConfig'
  return {
    __esModule: true,
    default: MockInitializerConfig,
  }
})

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('ConfigPage', () => {
  it('shows the Targets tab by default', () => {
    render(
      <TestWrapper>
        <ConfigPage activeTarget={null} onSetActiveTarget={jest.fn()} />
      </TestWrapper>,
    )

    expect(screen.getByTestId('target-config-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('initializer-config-panel')).not.toBeInTheDocument()
  })

  it('switches to the Initializers tab on click', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <ConfigPage activeTarget={null} onSetActiveTarget={jest.fn()} />
      </TestWrapper>,
    )

    await user.click(screen.getByTestId('config-tab-initializers'))

    expect(screen.getByTestId('initializer-config-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('target-config-panel')).not.toBeInTheDocument()
  })

  it('switches back to the Targets tab on click', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <ConfigPage activeTarget={null} onSetActiveTarget={jest.fn()} />
      </TestWrapper>,
    )

    await user.click(screen.getByTestId('config-tab-initializers'))
    await user.click(screen.getByTestId('config-tab-targets'))

    expect(screen.getByTestId('target-config-panel')).toBeInTheDocument()
    expect(screen.queryByTestId('initializer-config-panel')).not.toBeInTheDocument()
  })
})
