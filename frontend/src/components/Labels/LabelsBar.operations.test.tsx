import { useState } from 'react'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { labelsApi, operationsApi } from '@/services/api'
import type { Operation } from '@/types'
import LabelsBar from './LabelsBar'
import { DEFAULT_GLOBAL_LABELS } from './labelDefaults'

jest.mock('@/services/api', () => ({
  labelsApi: { getLabels: jest.fn() },
  operationsApi: { list: jest.fn(), create: jest.fn() },
}))

const SAVED: Operation = { id: 'saved-1', name: 'Engagement / α% & #One', created_at: '2026-10-07T16:00:00Z' }
const onChange = jest.fn()

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function Harness({ initial = { operator: 'alice' } }: { initial?: Record<string, string> }) {
  const [labels, setLabels] = useState(initial)
  return <LabelsBar labels={labels} onLabelsChange={next => { onChange(next); setLabels(next) }} />
}

function renderBar(initial?: Record<string, string>): void {
  render(<TestWrapper><Harness initial={initial} /></TestWrapper>)
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(labelsApi.getLabels).mockResolvedValue({ source: 'attacks', labels: { operation: ['unsaved_history'] } })
  jest.mocked(operationsApi.list).mockResolvedValue({ items: [SAVED] })
  jest.mocked(operationsApi.create).mockResolvedValue(SAVED)
})

describe('saved operation workflow', () => {
  it('has no fresh operation placeholder', () => {
    expect(DEFAULT_GLOBAL_LABELS).not.toHaveProperty('operation')
  })

  it('shows persistent named controls and pins creation above filtered choices', async () => {
    const user = userEvent.setup()
    renderBar()
    expect(screen.getByRole('textbox', { name: 'Operator' })).toHaveValue('alice')
    expect(screen.getByRole('combobox', { name: 'Operation' })).toHaveValue('')
    expect(screen.queryByRole('button', { name: 'New operation' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await screen.findByRole('option', { name: SAVED.name })
    expect(screen.getAllByRole('option')[0]).toHaveTextContent('New operation…')
    await user.type(screen.getByRole('combobox', { name: 'Operation' }), 'no match')
    expect(screen.getAllByRole('option')[0]).toHaveTextContent('New operation…')
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
  })

  it('commits valid operator input, rejects invalid values and cancels with Escape', async () => {
    const user = userEvent.setup()
    renderBar()
    const input = screen.getByRole('textbox', { name: 'Operator' })
    await user.clear(input)
    await user.type(input, 'BOB{Enter}')
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'bob' })
    await user.clear(input)
    await user.type(input, 'invalid value{Enter}')
    expect(screen.getByText('Only lowercase letters, numbers, underscores')).toBeInTheDocument()
    expect(onChange).toHaveBeenCalledTimes(1)
    await user.keyboard('{Escape}')
    expect(input).toHaveValue('bob')
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('keeps a signed-in operator read-only', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><LabelsBar labels={{ operator: 'alice' }} onLabelsChange={onChange} operatorReadOnly /></TestWrapper>)
    const input = screen.getByRole('textbox', { name: 'Signed-in operator' })
    expect(input).toHaveAttribute('readonly')
    await user.type(input, 'bob')
    expect(input).toHaveValue('alice')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('selects saved spelling exactly without offering observed or typed unsaved labels', async () => {
    const user = userEvent.setup()
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    const input = screen.getByRole('combobox', { name: 'Operation' })
    await user.type(input, 'engagement')
    await user.click(await screen.findByRole('option', { name: SAVED.name }))
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice', operation: SAVED.name })
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.type(screen.getByRole('combobox', { name: 'Operation' }), 'not saved')
    expect(screen.queryByRole('option', { name: /create/i })).not.toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'unsaved_history' })).not.toBeInTheDocument()
    await user.keyboard('{Enter}{Escape}')
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it('does not offer a legacy selection as a saved choice and allows removing it', async () => {
    const user = userEvent.setup()
    renderBar({ operator: 'alice', operation: 'Legacy / LABEL' })
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await screen.findByRole('option', { name: SAVED.name })
    expect(screen.queryByRole('option', { name: 'Legacy / LABEL' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('button', { name: 'Remove operation label' }))
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice' })
    expect(screen.getByRole('combobox', { name: 'Operation' })).toBeInTheDocument()
  })

  it('creates inline, immediately selects the saved response, and restores focus', async () => {
    const user = userEvent.setup()
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), ` ${SAVED.name} `)
    await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(operationsApi.create).toHaveBeenCalledWith({ name: ` ${SAVED.name} ` })
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice', operation: SAVED.name })
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Operation' })).toHaveFocus())
  })

  it('recovers duplicate names by selecting the existing operation in place', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.create).mockRejectedValue({
      isAxiosError: true, response: { status: 409, data: { detail: { operation: SAVED } } },
    })
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), SAVED.name.toUpperCase())
    await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
    await user.click(await within(dialog).findByRole('button', { name: 'Use existing' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice', operation: SAVED.name })
    expect(operationsApi.create).toHaveBeenCalledTimes(1)
  })

  it('preserves input on save failure without reporting selection', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.create).mockRejectedValue(new Error('Storage unavailable'))
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByRole('textbox', { name: 'Name' }), 'Keep me')
    await user.click(within(dialog).getByRole('button', { name: 'Create operation' }))
    expect(await within(dialog).findByText('Storage unavailable')).toBeInTheDocument()
    expect(within(dialog).getByRole('textbox', { name: 'Name' })).toHaveValue('Keep me')
    expect(onChange).not.toHaveBeenCalled()
  })

  it.each(['Cancel', 'Escape'])('discards the inline draft with %s', async (action: string) => {
    const user = userEvent.setup()
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    await user.type(screen.getByRole('textbox', { name: 'Name' }), 'Discard me')
    if (action === 'Cancel') await user.click(screen.getByRole('button', { name: 'Cancel' }))
    else await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.click(screen.getByRole('option', { name: 'New operation…' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveValue('')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('reports picker failure and can retry without accepting typed names', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.list).mockRejectedValueOnce(new Error('List unavailable'))
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    expect(await screen.findByText(/list unavailable/i)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry operations' }))
    await user.click(await screen.findByRole('option', { name: SAVED.name }))
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice', operation: SAVED.name })
  })

  it('ignores a late list response from a dismissed edit', async () => {
    const user = userEvent.setup()
    let finish: (value: { items: Operation[] }) => void = () => { throw new Error('Request not started') }
    jest.mocked(operationsApi.list).mockImplementationOnce(() => new Promise(resolve => { finish = resolve }))
    renderBar()
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await user.keyboard('{Escape}')
    await user.click(screen.getByRole('combobox', { name: 'Operation' }))
    await screen.findByRole('option', { name: SAVED.name })
    await act(async () => { finish({ items: [{ ...SAVED, name: 'Stale operation' }] }) })
    expect(screen.queryByRole('option', { name: 'Stale operation' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: SAVED.name }))
    expect(onChange).toHaveBeenLastCalledWith({ operator: 'alice', operation: SAVED.name })
  })
})
