import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ChatInputArea from './ChatInputArea'

describe('ChatInputArea seed inspiration', () => {
  it('shows an accessible browse action for an empty unsaved chat without sending', async () => {
    const user = userEvent.setup()
    const onBrowseSeedPrompts = jest.fn()
    const onSend = jest.fn().mockResolvedValue({ status: 'sent', clearDraft: false })

    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea
          onSend={onSend}
          onToggleConverterPanel={jest.fn()}
          isConverterPanelOpen={false}
          onInputChange={jest.fn()}
          onAttachmentsChange={jest.fn()}
          onClearConversion={jest.fn()}
          onConvertedValueChange={jest.fn()}
          onClearMediaConversion={jest.fn()}
          onBrowseSeedPrompts={onBrowseSeedPrompts}
          showSeedInspiration
          fileDropEnabled={false}
        />
      </FluentProvider>,
    )

    expect(screen.getByTestId('seed-inspiration-callout')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Browse seed prompts' }))

    expect(onBrowseSeedPrompts).toHaveBeenCalledTimes(1)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('keeps the composer entry keyboard accessible for a nonempty draft without sending', async () => {
    const user = userEvent.setup()
    const onBrowseSeedPrompts = jest.fn()
    const onSend = jest.fn().mockResolvedValue({ status: 'sent', clearDraft: false })
    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea
          onSend={onSend}
          onToggleConverterPanel={jest.fn()}
          isConverterPanelOpen={false}
          onInputChange={jest.fn()}
          onAttachmentsChange={jest.fn()}
          onClearConversion={jest.fn()}
          onConvertedValueChange={jest.fn()}
          onClearMediaConversion={jest.fn()}
          onBrowseSeedPrompts={onBrowseSeedPrompts}
          initialText="Keep this draft"
          showSeedInspiration={false}
          fileDropEnabled={false}
        />
      </FluentProvider>,
    )

    expect(screen.queryByTestId('seed-inspiration-callout')).not.toBeInTheDocument()
    const browse = screen.getByRole('button', { name: 'Browse seed prompts from composer' })
    browse.focus()
    await user.keyboard('{Enter}')
    expect(onBrowseSeedPrompts).toHaveBeenCalledTimes(1)
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Keep this draft')
  })
})
