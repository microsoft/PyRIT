import { render, screen } from '@testing-library/react'
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

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

describe('ConfigPage', () => {
  it('renders the target configuration panel', () => {
    render(
      <TestWrapper>
        <ConfigPage activeTarget={null} onSetActiveTarget={jest.fn()} />
      </TestWrapper>,
    )

    expect(screen.getByTestId('target-config-panel')).toBeInTheDocument()
  })
})

