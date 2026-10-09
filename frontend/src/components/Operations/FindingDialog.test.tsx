import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import FindingDialog from './FindingDialog'
import { operationsApi } from '@/services/api'

jest.mock('@/services/api', () => ({ operationsApi: { getFindingOptions: jest.fn() } }))

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(operationsApi.getFindingOptions).mockResolvedValue({ harm_types: ['Hate Speech', 'Malware', 'Other'] })
})

describe('FindingDialog', () => {
  it('uses the same defaults and severity choices and submits only finding fields', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    expect(screen.getByRole('combobox', { name: 'Severity' })).toHaveValue('moderate')
    expect(within(screen.getByRole('combobox', { name: 'Severity' })).getAllByRole('option').map(option => option.textContent)).toEqual([
      'Critical', 'Important', 'Moderate', 'Low', 'Informational', 'Other',
    ])
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'critical')
    await user.type(screen.getByRole('textbox', { name: 'Description' }), 'Notes')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    await waitFor(() => { expect(onSave).toHaveBeenCalledWith({
      title: 'Human assessment', severity: 'critical', description: 'Notes',
      severity_other: null, harm_type: null, harm_type_other: null,
    }) })
  })

  it('retains an edited draft on error and permits a corrected retry', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValueOnce(new Error('Storage offline')).mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'low', description: 'Details' }}
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    expect(screen.getByRole('heading', { name: 'Edit finding' })).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Title' }), ' edited')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByText('Storage offline')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Existing edited')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    await waitFor(() => { expect(screen.queryByText('Storage offline')).not.toBeInTheDocument() })
    expect(onSave).toHaveBeenCalledTimes(2)
  })

  it('requires custom Other text, preserves it on error, and clears it when returning to presets', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValueOnce(new Error('Offline')).mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Custom')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'other')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Other severity' }), '  Team severity  ')
    await user.click(screen.getByRole('combobox', { name: 'Harm-type' }))
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'Other', exact: true }))
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Other harm-type' }), '  Team harm  ')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByText('Offline')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Other severity' })).toHaveValue('  Team severity  ')
    expect(screen.getByRole('textbox', { name: 'Other harm-type' })).toHaveValue('  Team harm  ')
    expect(onSave).toHaveBeenCalledWith({
      title: 'Custom', description: '', severity: 'other', severity_other: '  Team severity  ',
      harm_type: 'Other', harm_type_other: '  Team harm  ',
    })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'low')
    await user.click(screen.getByRole('combobox', { name: 'Harm-type' }))
    await user.click(await screen.findByRole('option', { name: 'Not set', exact: true }))
    expect(screen.queryByRole('textbox', { name: 'Other severity' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Other harm-type' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(onSave).toHaveBeenLastCalledWith({
      title: 'Custom', description: '', severity: 'low', severity_other: null, harm_type: null, harm_type_other: null,
    })
  })

  it('searches canonical harm choices without treating typed search text as a custom category', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Search')
    await user.type(screen.getByRole('combobox', { name: 'Harm-type' }), 'mal')
    expect(await screen.findByRole('option', { name: 'Malware' })).toBeInTheDocument()
    expect(screen.queryByRole('option', { name: 'Hate Speech' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('option', { name: 'Malware' }))
    expect(screen.getByRole('combobox', { name: 'Harm-type' })).toHaveValue('Malware')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ harm_type: 'Malware', harm_type_other: null }))
  })

  it('reports category loading failures, retains an edit draft, and retries without clearing fields', async () => {
    const user = userEvent.setup()
    jest.mocked(operationsApi.getFindingOptions).mockRejectedValueOnce(new Error('Categories offline'))
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{
        title: 'Existing', description: '', severity: 'other', severity_other: 'Team severity',
        harm_type: 'Other', harm_type_other: 'Team harm',
      }} onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    expect(await screen.findByText(/Could not load harm categories: Categories offline/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: 'Other harm-type' })).toHaveValue('Team harm')
    await user.click(screen.getByRole('button', { name: 'Retry harm categories' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ harm_type_other: 'Team harm', severity_other: 'Team severity' }))
  })

  it('preserves custom harm text when the current Other option is selected again', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', description: '', severity: 'low', harm_type: 'Other', harm_type_other: 'Keep custom harm' }}
      onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())
    await user.click(screen.getByRole('combobox', { name: 'Harm-type' }))
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'Other', exact: true }))
    expect(screen.getByRole('textbox', { name: 'Other harm-type' })).toHaveValue('Keep custom harm')
  })

  it('blocks blank custom values even when the form is submitted programmatically', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn()
    render(<FluentProvider theme={webLightTheme}><FindingDialog
      initialValues={{ title: 'Title', description: '', severity: 'other', severity_other: '  ' }}
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())
    await user.click(screen.getByRole('textbox', { name: 'Other severity' }))
    fireEvent.submit(screen.getByRole('button', { name: 'Save finding' }).closest('form')!)
    expect(onSave).not.toHaveBeenCalled()
  })

  it('does not require taxonomy loading to retry an already-created finding attachment', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog recovery={<p>Saved finding recovery</p>}
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    expect(operationsApi.getFindingOptions).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Retry attachment' }))
    expect(onSave).toHaveBeenCalledTimes(1)
  })
})
