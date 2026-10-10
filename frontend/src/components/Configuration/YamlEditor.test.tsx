import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import YamlEditor from './YamlEditor'

function renderEditor(props: React.ComponentProps<typeof YamlEditor>): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <YamlEditor {...props} />
    </FluentProvider>,
  )
}

describe('YamlEditor', () => {
  it('should highlight YAML and report edits', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    renderEditor({ value: 'enabled: true\n', disabled: false, onChange })

    expect(screen.getByTestId('yaml-highlight').innerHTML).toContain('token key atrule')

    await user.type(screen.getByRole('textbox', { name: 'Configuration YAML' }), '#')

    expect(onChange).toHaveBeenCalledWith('enabled: true\n#')
  })

  it('should disable editing when requested', () => {
    renderEditor({ value: '', disabled: true, onChange: jest.fn() })

    expect(screen.getByRole('textbox', { name: 'Configuration YAML' })).toBeDisabled()
  })
})
