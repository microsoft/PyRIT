import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import FindingDialog from './FindingDialog'
import { operationsApi } from '@/services/api'

jest.mock('@/services/api', () => ({ operationsApi: { getFindingOptions: jest.fn() } }))

const ATTEMPT = { id: expect.any(String), attackResultId: '' }

const UUID_MESSAGE = 'Input should be a valid UUID, unable to parse string as an UUID'

/** Mirrors the backend's RFC 7807 422 response (see error_handlers.py). */
function validationError(errors: Array<{ field: string; message: string; code?: string }>): unknown {
  return {
    isAxiosError: true,
    message: 'Request failed with status code 422',
    response: {
      status: 422,
      data: {
        type: '/errors/validation-error', title: 'Validation Error', status: 422,
        detail: 'Request validation failed', errors,
      },
      headers: {}, config: {},
    },
  }
}

function axiosFailure(status: number, detail: string): unknown {
  return {
    isAxiosError: true,
    message: `Request failed with status code ${status}`,
    response: { status, data: { detail }, headers: {}, config: {} },
  }
}

function requestPageUnload(): Event {
  const event = new Event('beforeunload', { cancelable: true })
  fireEvent(window, event)
  return event
}

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
    }, ATTEMPT) })
  })

  it('arms the unsaved changes guard for a severity only change in a new finding', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())

    expect(requestPageUnload().defaultPrevented).toBe(false)

    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'critical')
    expect(requestPageUnload().defaultPrevented).toBe(true)
  })

  it('does not arm the unsaved changes guard for an untouched edit dialog', async () => {
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'low', description: 'Details' }}
      onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())

    expect(requestPageUnload().defaultPrevented).toBe(false)
  })

  it.each([
    ['Title', 'Existing'],
    ['Description', 'Details'],
    ['Other severity', 'Team severity'],
    ['Other harm-type', 'Team harm'],
  ])('protects a %s edit and disarms the guard when its original value is restored', async (label: string, originalValue: string) => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{
        title: 'Existing', description: 'Details', severity: 'other', severity_other: 'Team severity',
        harm_type: 'Other', harm_type_other: 'Team harm',
      }} onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())
    const field = screen.getByRole('textbox', { name: label })

    expect(requestPageUnload().defaultPrevented).toBe(false)
    await user.type(field, ' edited')
    expect(requestPageUnload().defaultPrevented).toBe(true)
    await user.clear(field)
    expect(requestPageUnload().defaultPrevented).toBe(true)
    await user.type(field, originalValue)
    expect(requestPageUnload().defaultPrevented).toBe(false)
  })

  it('protects a severity edit and disarms the guard when the original severity is restored', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'low', description: 'Details' }}
      onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())
    const severity = screen.getByRole('combobox', { name: 'Severity' })

    expect(requestPageUnload().defaultPrevented).toBe(false)
    await user.selectOptions(severity, 'critical')
    expect(requestPageUnload().defaultPrevented).toBe(true)
    await user.selectOptions(severity, 'low')
    expect(requestPageUnload().defaultPrevented).toBe(false)
  })

  it('protects a harm-type edit and disarms the guard when the original category is restored', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'low', description: 'Details', harm_type: 'Malware' }}
      onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    const harmType = screen.getByRole('combobox', { name: 'Harm-type' })
    await waitFor(() => expect(harmType).toBeEnabled())

    expect(requestPageUnload().defaultPrevented).toBe(false)
    await user.click(harmType)
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'Hate Speech', exact: true }))
    expect(requestPageUnload().defaultPrevented).toBe(true)
    await user.click(harmType)
    await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: 'Malware', exact: true }))
    expect(requestPageUnload().defaultPrevented).toBe(false)
  })

  it('protects evidence changes, disarms the guard when cleared, and removes it on unmount', async () => {
    const user = userEvent.setup()
    const { unmount } = render(<FluentProvider theme={webLightTheme}><FindingDialog evidenceField
      onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Harm-type' })).toBeEnabled())
    const evidence = screen.getByRole('textbox', { name: 'Evidence attack ID' })

    expect(requestPageUnload().defaultPrevented).toBe(false)
    await user.type(evidence, 'attack-1')
    expect(requestPageUnload().defaultPrevented).toBe(true)
    await user.clear(evidence)
    expect(requestPageUnload().defaultPrevented).toBe(false)
    await user.type(evidence, 'attack-2')
    expect(requestPageUnload().defaultPrevented).toBe(true)
    unmount()
    expect(requestPageUnload().defaultPrevented).toBe(false)
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
    }, ATTEMPT)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'low')
    await user.click(screen.getByRole('combobox', { name: 'Harm-type' }))
    await user.click(await screen.findByRole('option', { name: 'Not set', exact: true }))
    expect(screen.queryByRole('textbox', { name: 'Other severity' })).not.toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Other harm-type' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(onSave).toHaveBeenLastCalledWith({
      title: 'Custom', description: '', severity: 'low', severity_other: null, harm_type: null, harm_type_other: null,
    }, ATTEMPT)
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
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ harm_type: 'Malware', harm_type_other: null }), ATTEMPT)
  })

  it('keeps Other selectable when a category search has no match', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    const picker = screen.getByRole('combobox', { name: 'Harm-type' })
    await waitFor(() => expect(picker).toBeEnabled())
    await user.type(picker, 'organization-specific-risk')
    const listbox = await screen.findByRole('listbox')
    expect(within(listbox).getByRole('option', { name: 'Other', exact: true })).toBeEnabled()
    await user.click(within(listbox).getByRole('option', { name: 'Other', exact: true }))
    expect(screen.getByRole('textbox', { name: 'Other harm-type' })).toBeInTheDocument()
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
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ harm_type_other: 'Team harm', severity_other: 'Team severity' }), ATTEMPT)
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

  it('keeps one finding ID while a retry sends the same input, and starts a new one after an edit', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValue(new Error('Response lost'))
    render(<FluentProvider theme={webLightTheme}><FindingDialog onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Once')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    await user.click(await screen.findByRole('button', { name: 'Save finding' }))
    await user.type(screen.getByRole('textbox', { name: 'Title' }), ' edited')
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3))
    const [first, retry, edited] = onSave.mock.calls.map(([, attempt]) => attempt.id)
    expect(retry).toBe(first)
    expect(edited).not.toBe(first)
  })

  it('offers an optional evidence attack only when asked and sends it trimmed', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog evidenceField onSave={onSave} onClose={jest.fn()} />
    </FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'With evidence')
    await user.type(screen.getByRole('textbox', { name: 'Evidence attack ID' }), '  attack-1 ')
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ title: 'With evidence' }), {
      id: expect.any(String), attackResultId: 'attack-1',
    })
  })
})

describe('FindingDialog server field validation', () => {
  it('shows the server explanation on the invalid control and guards resubmission until corrected', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn()
      .mockRejectedValueOnce(validationError([
        { field: 'body.attack_result_id', message: UUID_MESSAGE, code: 'uuid_parsing' },
      ]))
      .mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog evidenceField
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')
    await user.type(screen.getByRole('textbox', { name: 'Evidence attack ID' }), 'not-a-uuid')

    await user.click(screen.getByRole('button', { name: 'Save finding' }))

    const evidence = screen.getByRole('textbox', { name: 'Evidence attack ID' })
    expect(await screen.findByText(UUID_MESSAGE)).toBeInTheDocument()
    expect(evidence).toHaveAttribute('aria-invalid', 'true')
    expect(evidence).toHaveFocus()
    expect(screen.getByText(/Check Evidence attack ID/)).toBeInTheDocument()
    const saveButton = screen.getByRole('button', { name: 'Save finding' })
    expect(saveButton).toBeDisabled()
    fireEvent.submit(saveButton.closest('form')!)
    expect(onSave).toHaveBeenCalledTimes(1)

    await user.clear(evidence)
    await user.type(evidence, '123e4567-e89b-12d3-a456-426614174000')
    expect(screen.queryByText(UUID_MESSAGE)).not.toBeInTheDocument()
    expect(evidence).not.toHaveAttribute('aria-invalid', 'true')
    expect(saveButton).toBeEnabled()
    await user.click(saveButton)
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
  })

  it('flags several fields and clears each error only when its own value changes', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValue(validationError([
      { field: 'body.title', message: 'Title is already used' },
      { field: 'body.description', message: 'Description is not allowed' },
    ]))
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'low', description: 'Details' }}
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)

    await user.click(screen.getByRole('button', { name: 'Save finding' }))

    expect(await screen.findByText('Title is already used')).toBeInTheDocument()
    expect(screen.getByText('Description is not allowed')).toBeInTheDocument()
    expect(screen.getByText(/Check Title, Description/)).toBeInTheDocument()
    const title = screen.getByRole('textbox', { name: 'Title' })
    const description = screen.getByRole('textbox', { name: 'Description' })
    expect(title).toHaveAttribute('aria-invalid', 'true')
    expect(description).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()

    await user.type(title, ' revised')
    expect(title).not.toHaveAttribute('aria-invalid', 'true')
    expect(description).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()

    await user.type(description, ' revised')
    expect(description).not.toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled()
  })

  it('lists errors for fields the form does not render with their API paths', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValue(validationError([
      { field: 'body.attack_result_id', message: UUID_MESSAGE },
      { field: 'body.severity_other', message: 'Required for custom severity' },
    ]))
    render(<FluentProvider theme={webLightTheme}><FindingDialog
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')

    await user.click(screen.getByRole('button', { name: 'Save finding' }))

    expect(await screen.findByText(`body.attack_result_id: ${UUID_MESSAGE}`)).toBeInTheDocument()
    expect(screen.getByText('body.severity_other: Required for custom severity')).toBeInTheDocument()
    expect(screen.queryByText(/Check /)).not.toBeInTheDocument()
    // Nothing editable was flagged, so the user can retry rather than being trapped.
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled()
  })

  it('drops a flagged conditional field error when that field stops rendering', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValue(validationError([
      { field: 'body.severity_other', message: 'Custom severity is not allowed' },
    ]))
    render(<FluentProvider theme={webLightTheme}><FindingDialog editing
      initialValues={{ title: 'Existing', severity: 'other', severity_other: 'Team severity' }}
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)

    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByText('Custom severity is not allowed')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeDisabled()

    await user.selectOptions(screen.getByRole('combobox', { name: 'Severity' }), 'low')
    expect(screen.queryByRole('textbox', { name: 'Other severity' })).not.toBeInTheDocument()
    expect(screen.queryByText('Custom severity is not allowed')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled()
  })

  it('keeps the generic message and retry path for a 422 without field errors', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValueOnce(axiosFailure(422, 'Request validation failed'))
      .mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')

    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByText('Request validation failed')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Title' })).not.toHaveAttribute('aria-invalid', 'true')
    const saveButton = screen.getByRole('button', { name: 'Save finding' })
    expect(saveButton).toBeEnabled()
    await user.click(saveButton)
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
  })

  it('leaves non-validation failures retryable and unflagged', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockRejectedValueOnce(axiosFailure(500, 'Storage offline'))
      .mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}><FindingDialog
      onSave={onSave} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')

    await user.click(screen.getByRole('button', { name: 'Save finding' }))
    expect(await screen.findByText('Storage offline')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Title' })).not.toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByRole('button', { name: 'Save finding' })).toBeEnabled()
  })
})
