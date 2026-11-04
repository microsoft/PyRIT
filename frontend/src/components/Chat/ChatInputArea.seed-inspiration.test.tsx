import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ChatInputArea from './ChatInputArea'

describe('ChatInputArea seed inspiration', () => {
  it('shows an accessible browse action for an empty unsaved chat without sending', async () => {
    const user = userEvent.setup()
    const onBrowseSeedPrompts = jest.fn()
    const onSend = jest.fn().mockResolvedValue({ clearDraft: false })

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

  it('does not show the callout when the chat is not empty', () => {
    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea
          onSend={jest.fn().mockResolvedValue({ clearDraft: false })}
          onToggleConverterPanel={jest.fn()}
          isConverterPanelOpen={false}
          onInputChange={jest.fn()}
          onAttachmentsChange={jest.fn()}
          onClearConversion={jest.fn()}
          onConvertedValueChange={jest.fn()}
          onClearMediaConversion={jest.fn()}
          onBrowseSeedPrompts={jest.fn()}
          showSeedInspiration={false}
          fileDropEnabled={false}
        />
      </FluentProvider>,
    )

    expect(screen.queryByTestId('seed-inspiration-callout')).not.toBeInTheDocument()
  })
})
