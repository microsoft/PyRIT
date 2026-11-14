import React, { useState } from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import RetiredFieldValues from './RetiredFieldValues'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const RETIRED_VALUES = [
  { key: 'description', value: 'The assistant agreed to play a character and then\nexplained the restricted steps.' },
  { key: 'contoso_ticket', value: 'CONTOSO-42' },
]

function removeButtonName(key: string): string {
  return `Remove retired value ${key}`
}

interface RetiredFieldValuesHarnessProps {
  onRemove: (key: string) => void
  onUndo: () => void
}

// Holds the removed keys as the edit dialog does, so a test can follow a removal through to Undo.
function RetiredFieldValuesHarness({ onRemove, onUndo }: RetiredFieldValuesHarnessProps) {
  const [removed, setRemoved] = useState<ReadonlySet<string>>(new Set())
  return (
    <RetiredFieldValues values={RETIRED_VALUES} removed={removed}
      onRemove={(key: string) => {
        onRemove(key)
        setRemoved((previous) => new Set([...previous, key]))
      }}
      onUndo={() => {
        onUndo()
        setRemoved(new Set())
      }} />
  )
}

// jsdom has no default stylesheet, so an accessible name would gain a space around the title's <code> key.
async function findConfirmation(key: string): Promise<HTMLElement> {
  const dialog = await screen.findByRole('dialog')
  expect(within(dialog).getByRole('heading')).toHaveTextContent(`Remove ${key}?`)
  return dialog
}

async function confirmRemoval(user: UserEvent, key: string): Promise<void> {
  await user.click(await screen.findByRole('button', { name: removeButtonName(key) }))
  const dialog = await findConfirmation(key)
  await user.click(within(dialog).getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}

describe('RetiredFieldValues', () => {
  const defaultProps = {
    values: RETIRED_VALUES,
    removed: new Set<string>(),
    onRemove: jest.fn(),
    onUndo: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders nothing without retired values', () => {
    render(<TestWrapper><RetiredFieldValues {...defaultProps} values={[]} /></TestWrapper>)
    expect(screen.queryByText('Retired fields · read-only')).not.toBeInTheDocument()
    expect(screen.queryByRole('group')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('lists each retired value with its raw key and a Remove button', () => {
    render(<TestWrapper><RetiredFieldValues {...defaultProps} /></TestWrapper>)
    const group = screen.getByRole('group', { name: 'Retired fields · read-only' })
    const items = within(group).getAllByRole('listitem')
    expect(items).toHaveLength(2)
    const [description, ticket] = items
    expect(within(description).getByText('description')).toBeInTheDocument()
    expect(within(description).getByText(/The assistant agreed/)).toHaveTextContent(
      'The assistant agreed to play a character and then\nexplained the restricted steps.',
      { normalizeWhitespace: false },
    )
    expect(within(description).getByRole('button', { name: removeButtonName('description') })).toBeEnabled()
    expect(within(ticket).getByText('contoso_ticket')).toBeInTheDocument()
    expect(within(ticket).getByText('CONTOSO-42')).toBeInTheDocument()
    expect(within(ticket).getByRole('button', { name: removeButtonName('contoso_ticket') })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Undo' })).not.toBeInTheDocument()
  })

  it('asks before removing and keeps the value on Keep it', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><RetiredFieldValues {...defaultProps} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: removeButtonName('description') }))
    const dialog = await findConfirmation('description')
    expect(dialog).toHaveTextContent(
      'This deletes its text from the finding when you save. Copy anything you need first. '
      + 'Cancel on the edit dialog still brings it back.',
    )
    expect(within(dialog).getByRole('button', { name: 'Remove' })).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Keep it' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(await screen.findByRole('button', { name: removeButtonName('description') })).toBeInTheDocument()
    expect(defaultProps.onRemove).not.toHaveBeenCalled()
  })

  it('reports a confirmed removal and offers Undo', async () => {
    const user = userEvent.setup()
    const onRemove = jest.fn()
    const onUndo = jest.fn()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={onRemove} onUndo={onUndo} /></TestWrapper>)
    await confirmRemoval(user, 'description')
    expect(onRemove).toHaveBeenCalledTimes(1)
    expect(onRemove).toHaveBeenCalledWith('description')
    expect(await screen.findByRole('button', { name: removeButtonName('contoso_ticket') })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: removeButtonName('description') })).not.toBeInTheDocument()
    expect(screen.getByText('1 retired value is removed when you save.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(onUndo).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: removeButtonName('description') })).toBeInTheDocument()
    expect(screen.queryByText(/removed when you save/)).not.toBeInTheDocument()
  })

  it('counts several removals', async () => {
    const user = userEvent.setup()
    const onRemove = jest.fn()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={onRemove} onUndo={jest.fn()} /></TestWrapper>)
    await confirmRemoval(user, 'description')
    await confirmRemoval(user, 'contoso_ticket')
    expect(onRemove).toHaveBeenNthCalledWith(1, 'description')
    expect(onRemove).toHaveBeenNthCalledWith(2, 'contoso_ticket')
    expect(await screen.findByRole('button', { name: 'Undo' })).toBeInTheDocument()
    expect(screen.getByText('2 retired values are removed when you save.')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Retired fields · read-only' })).not.toBeInTheDocument()
  })

  it('moves focus to Undo after a removal is confirmed', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={jest.fn()} onUndo={jest.fn()} /></TestWrapper>)
    await confirmRemoval(user, 'description')
    expect(await screen.findByRole('button', { name: 'Undo' })).toHaveFocus()
  })

  // jsdom fires no focusout when the dialog removes the focused button, so Tabster only restores focus here after
  // keyboard use. The e2e spec covers a mouse click on Keep it.
  it('returns focus to the Remove button on Keep it', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={jest.fn()} onUndo={jest.fn()} /></TestWrapper>)
    const removeButton = screen.getByRole('button', { name: removeButtonName('contoso_ticket') })
    await user.click(removeButton)
    const dialog = await findConfirmation('contoso_ticket')
    expect(within(dialog).getByRole('button', { name: 'Keep it' })).toHaveFocus()
    await user.keyboard('{Enter}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(removeButton).toHaveFocus())
  })

  it('returns focus to the Remove button on Escape', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={jest.fn()} onUndo={jest.fn()} /></TestWrapper>)
    const removeButton = screen.getByRole('button', { name: removeButtonName('contoso_ticket') })
    await user.click(removeButton)
    await findConfirmation('contoso_ticket')
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(removeButton).toHaveFocus())
  })

  it('moves focus to the first Remove button after Undo', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><RetiredFieldValuesHarness onRemove={jest.fn()} onUndo={jest.fn()} /></TestWrapper>)
    await confirmRemoval(user, 'contoso_ticket')
    await user.click(await screen.findByRole('button', { name: 'Undo' }))
    expect(screen.getByRole('button', { name: removeButtonName('description') })).toHaveFocus()
  })

  it('disables every Remove button and Undo while disabled', () => {
    const values = [...RETIRED_VALUES, { key: 'contoso_owner', value: 'Contoso red team' }]
    render(<TestWrapper>
      <RetiredFieldValues {...defaultProps} values={values} removed={new Set(['contoso_ticket'])} disabled />
    </TestWrapper>)
    const removeButtons = screen.getAllByRole('button', { name: /^Remove retired value / })
    expect(removeButtons).toHaveLength(2)
    for (const button of removeButtons) expect(button).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Undo' })).toBeDisabled()
  })
})
