import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router'

import { operationsApi } from '@/services/api'
import type { Operation } from '@/types'
import OperationsPage from './OperationsPage'

jest.mock('@/services/api', () => ({
  operationsApi: { list: jest.fn(), create: jest.fn() },
}))

const OPERATION: Operation = { id: 'op-1', name: 'Operation A', created_at: '2026-10-06T20:00:00Z' }

function LocationProbe() {
  return <span data-testid="location">{useLocation().pathname}</span>
}

function renderPage(): void {
  render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={['/operations']}>
        <Routes>
          <Route path="/operations" element={<OperationsPage />} />
          <Route path="/operations/:operationId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

function conflict(existing: Operation): unknown {
  return {
    isAxiosError: true,
    response: { status: 409, data: { detail: { message: 'exists', operation: existing } } },
  }
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(operationsApi.list).mockResolvedValue({ items: [OPERATION] })
})

it('lists operations as links to their pages', async () => {
  const user = userEvent.setup()
  renderPage()
  await user.click(await screen.findByRole('link', { name: /Operation A/ }))
  expect(screen.getByTestId('location')).toHaveTextContent('/operations/op-1')
})

it('shows an empty state when no operations exist', async () => {
  jest.mocked(operationsApi.list).mockResolvedValue({ items: [] })
  renderPage()
  expect(await screen.findByText(/no operations yet/i)).toBeInTheDocument()
})

it('creates an operation and opens it', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.create).mockResolvedValue({ ...OPERATION, id: 'op-2', name: 'Case' })
  renderPage()
  await screen.findByRole('link', { name: /Operation A/ })
  await user.click(screen.getByRole('button', { name: 'New operation' }))
  const dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('button', { name: 'Create operation' })).toBeDisabled()
  await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), ' Case ')
  await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
  expect(await screen.findByTestId('location')).toHaveTextContent('/operations/op-2')
  expect(operationsApi.create).toHaveBeenCalledWith({ name: ' Case ' })
})

it('links to the existing operation when the name is taken', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.create).mockRejectedValue(conflict(OPERATION))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New operation' }))
  const dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), ' operation a ')
  await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
  expect(await within(dialog).findByText(/already exists/i)).toBeInTheDocument()
  await user.click(within(dialog).getByRole('link', { name: 'Open Operation A' }))
  expect(await screen.findByTestId('location')).toHaveTextContent('/operations/op-1')
})

it('discards the draft when the dialog is cancelled', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.create).mockRejectedValue(new Error('Storage unavailable'))
  renderPage()
  await user.click(await screen.findByRole('button', { name: 'New operation' }))
  let dialog = screen.getByRole('dialog')
  await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Draft')
  await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
  await within(dialog).findByText(/storage unavailable/i)
  await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
  await waitFor(() => { expect(screen.queryByRole('dialog', { hidden: true })).not.toBeInTheDocument() })
  await user.click(await screen.findByRole('button', { name: 'New operation' }))
  dialog = screen.getByRole('dialog')
  expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('')
  expect(within(dialog).queryByText(/storage unavailable/i)).not.toBeInTheDocument()
})

it('offers a retry when operations cannot be loaded', async () => {
  const user = userEvent.setup()
  jest.mocked(operationsApi.list).mockRejectedValueOnce(new Error('List unavailable'))
  renderPage()
  expect(await screen.findByText(/list unavailable/i)).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Retry' }))
  expect(await screen.findByRole('link', { name: /Operation A/ })).toBeInTheDocument()
})
