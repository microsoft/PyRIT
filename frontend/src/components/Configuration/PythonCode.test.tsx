import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { PythonCodeBlock, PythonCodeEditor } from './PythonCode'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('PythonCode', () => {
  it('should render highlighted read-only source', () => {
    render(
      <TestWrapper>
        <PythonCodeBlock source="class Example: pass" ariaLabel="Stored Python source" />
      </TestWrapper>,
    )

    expect(screen.getByLabelText('Stored Python source').innerHTML).toContain('token keyword')
  })

  it('should report editor changes', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <PythonCodeEditor source="pass" disabled={false} onChange={onChange} />
      </TestWrapper>,
    )

    await user.type(screen.getByRole('textbox', { name: 'Python source' }), '#')

    expect(onChange).toHaveBeenCalledWith('pass#')
  })

  it('should disable editing when requested', () => {
    render(
      <TestWrapper>
        <PythonCodeEditor source="pass" disabled onChange={jest.fn()} />
      </TestWrapper>,
    )

    expect(screen.getByRole('textbox', { name: 'Python source' })).toBeDisabled()
  })
})
