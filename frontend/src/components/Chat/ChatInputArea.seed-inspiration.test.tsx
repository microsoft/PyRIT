import type { ComponentProps } from 'react'

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import ChatInputArea from './ChatInputArea'

jest.mock('@/services/api', () => ({
  mediaApi: { upload: jest.fn().mockResolvedValue({ value: '/stored/draft.png' }) },
}))

describe('ChatInputArea seed inspiration', () => {
  const defaultProps: ComponentProps<typeof ChatInputArea> = {
    onSend: jest.fn().mockResolvedValue({ status: 'sent', clearDraft: false }),
    onToggleConverterPanel: jest.fn(),
    isConverterPanelOpen: false,
    onInputChange: jest.fn(),
    onAttachmentsChange: jest.fn(),
    onClearConversion: jest.fn(),
    onConvertedValueChange: jest.fn(),
    onClearMediaConversion: jest.fn(),
    onBrowseSeedPrompts: jest.fn(),
    fileDropEnabled: false,
  }

  beforeEach(() => jest.clearAllMocks())

  const gatedStates: Array<Partial<ComponentProps<typeof ChatInputArea>>> = [
    { disabled: true, disabledReasons: ['This target only supports single-turn conversations.'] },
    { targetResolutionStatus: 'unavailable' },
    { operatorLocked: true, attackOperator: 'another operator' },
    { crossTargetLocked: true },
  ]

  it.each(gatedStates)('keeps read-only browsing available when sending is gated by %j', async (props: Partial<ComponentProps<typeof ChatInputArea>>) => {
    const user = userEvent.setup()
    const onBrowseSeedPrompts = jest.fn()
    const onSend = jest.fn()
    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea {...props}
          onSend={onSend} onBrowseSeedPrompts={onBrowseSeedPrompts}
          onToggleConverterPanel={jest.fn()} isConverterPanelOpen={false}
          onInputChange={jest.fn()} onAttachmentsChange={jest.fn()}
          onClearConversion={jest.fn()} onConvertedValueChange={jest.fn()}
          onClearMediaConversion={jest.fn()} fileDropEnabled={false} />
      </FluentProvider>,
    )
    const browse = screen.getByRole('button', { name: 'Browse seed prompts from composer' })
    await user.click(browse)
    browse.focus()
    await user.keyboard('{Enter}')
    expect(onBrowseSeedPrompts).toHaveBeenCalledTimes(2)
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.queryByRole('dialog', { name: 'Prompt input is disabled' })).not.toBeInTheDocument()
  })

  it('shows a compact helper beside the persistent browse action without sending', async () => {
    const user = userEvent.setup()
    const onBrowseSeedPrompts = jest.fn()
    const onSend = jest.fn().mockResolvedValue({ status: 'sent', clearDraft: false })

    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea {...defaultProps} onSend={onSend}
          onBrowseSeedPrompts={onBrowseSeedPrompts} showSeedInspiration />
      </FluentProvider>,
    )

    expect(screen.getByRole('button', { name: 'Browse seed prompts from composer' })).toBeVisible()
    expect(screen.queryByTestId('seed-inspiration-callout')).not.toBeInTheDocument()
    const helper = screen.getByRole('button', { name: 'Need inspiration? Browse seed prompts' })
    await user.click(helper)
    helper.focus()
    await user.keyboard('{Enter}')

    expect(onBrowseSeedPrompts).toHaveBeenCalledTimes(2)
    expect(onSend).not.toHaveBeenCalled()
  })

  it('keeps the composer entry keyboard accessible after the first turn without sending', async () => {
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

    expect(screen.queryByRole('button', { name: 'Need inspiration? Browse seed prompts' })).not.toBeInTheDocument()
    const browse = screen.getByRole('button', { name: 'Browse seed prompts from composer' })
    browse.focus()
    await user.keyboard('{Enter}')
    expect(onBrowseSeedPrompts).toHaveBeenCalledTimes(1)
    expect(onSend).not.toHaveBeenCalled()
    expect(screen.getByPlaceholderText('Type prompt here')).toHaveValue('Keep this draft')
  })

  it('keeps the helper while typing and attaching to the first draft', async () => {
    const user = userEvent.setup()
    render(
      <FluentProvider theme={webLightTheme}>
        <ChatInputArea {...defaultProps} showSeedInspiration />
      </FluentProvider>,
    )
    await user.type(screen.getByPlaceholderText('Type prompt here'), 'My first draft')
    await user.upload(screen.getByTestId('file-input'), new File(['image'], 'draft.png', { type: 'image/png' }))
    expect(screen.getByRole('button', { name: 'Need inspiration? Browse seed prompts' })).toBeVisible()
    expect(screen.getByRole('button', { name: 'Remove draft.png' })).toBeVisible()
    expect(defaultProps.onSend).not.toHaveBeenCalled()
  })
})
