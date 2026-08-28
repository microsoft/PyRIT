import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import DotenvEditor from './DotenvEditor'

function renderEditor(props: React.ComponentProps<typeof DotenvEditor>): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <DotenvEditor {...props} />
    </FluentProvider>,
  )
}

describe('DotenvEditor', () => {
  it('should highlight dotenv content and report edits', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    renderEditor({ value: 'ENABLED=true\n', disabled: false, onChange })

    expect(screen.getByTestId('dotenv-highlight').innerHTML).toContain('token key atrule')

    await user.type(screen.getByRole('textbox', { name: 'Environment file contents' }), '#')

    expect(onChange).toHaveBeenCalledWith('ENABLED=true\n#')
  })

  it('should disable editing when requested', () => {
    renderEditor({ value: '', disabled: true, onChange: jest.fn() })

    expect(screen.getByRole('textbox', { name: 'Environment file contents' })).toBeDisabled()
  })
})
