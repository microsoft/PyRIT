import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { operationsApi } from '@/services/api'
import OperationEditDialog from './OperationEditDialog'
import type { Operation } from '@/types'

jest.mock('@/services/api', () => ({ operationsApi: { update: jest.fn().mockResolvedValue({}) } }))

const operation: Operation = { id: 'op-1', name: 'Weekly review', created_at: '2026-01-01T00:00:00Z' }

function renderDialog(props: Partial<Parameters<typeof OperationEditDialog>[0]> = {}) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <OperationEditDialog operation={operation} onSaved={jest.fn()} {...props} />
    </FluentProvider>,
  )
}

it('does not send an untouched field when the operation prop refreshes while editing', async () => {
  const user = userEvent.setup()
  const { rerender } = renderDialog()
  await user.click(await screen.findByRole('button', { name: /edit/i }))
  const dialog = screen.getByRole('dialog')

  // The operation refetches while the dialog is open: the description changed elsewhere.
  const refreshed = { ...operation, description: 'changed elsewhere' }
  rerender(
    <FluentProvider theme={webLightTheme}>
      <OperationEditDialog operation={refreshed} onSaved={jest.fn()} />
    </FluentProvider>,
  )

  await user.click(within(dialog).getByRole('button', { name: /save/i }))
  const call = (operationsApi.update as jest.Mock).mock.calls[0]
  expect(call[0]).toBe('op-1')
  expect(call[1]).toEqual({})
})
