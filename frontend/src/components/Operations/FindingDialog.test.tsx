import React, { useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { deferred } from '@/test-utils/deferred'
import { findRemovalConfirmation, pick } from '@/test-utils/findingDialog'
import { CONTOSO_FINDING_FIELDS, DEFAULT_FINDING_FIELDS_FIXTURE } from '@/test-utils/findingFieldsFixtures'
import type { FindingCreate, FindingFields } from '@/types'

import FindingDialog, { type FindingDraft } from './FindingDialog'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const ATTEMPT = { id: expect.any(String), attackResultId: '' }

const CONTOSO_STEPS = '1. Ask the Contoso assistant to repeat its instructions.'
const CONTOSO_EDIT: FindingCreate = {
  title: 'Contoso assistant repeats its system prompt',
  severity: 'sev1',
  field_values: {
    severity_rationale: 'customer_impact',
    category: 'model_security',
    subcategory: 'system_prompt_leak',
    steps: CONTOSO_STEPS,
    description: 'Recorded before Contoso configured its own fields.',
  },
}
const CONTOSO_CURRENT_VALUES = {
  severity_rationale: 'customer_impact',
  category: 'model_security',
  subcategory: 'system_prompt_leak',
  steps: CONTOSO_STEPS,
}

interface DialogOptions {
  fields?: FindingFields
  initialValues?: FindingCreate
  editing?: boolean
  evidenceField?: boolean
  onSave?: (draft: FindingDraft, attempt: { id: string; attackResultId: string }) => Promise<void>
  onClose?: () => void
}

function renderDialog({
  fields = DEFAULT_FINDING_FIELDS_FIXTURE, onSave = jest.fn().mockResolvedValue(undefined), onClose = jest.fn(),
  ...props
}: DialogOptions = {}): void {
  render(<TestWrapper><FindingDialog fields={fields} onSave={onSave} onClose={onClose} {...props} /></TestWrapper>)
}

// Holds the dialog the way the Operation page does, so Cancel can close it and Edit can reopen it.
function EditHarness({ onSave }: { onSave: (draft: FindingDraft) => Promise<void> }) {
  const [open, setOpen] = useState(false)
  return <>
    <button onClick={() => { setOpen(true) }}>Edit Contoso finding</button>
    {open && <FindingDialog fields={CONTOSO_FINDING_FIELDS} editing initialValues={CONTOSO_EDIT}
      onSave={onSave} onClose={() => { setOpen(false) }} />}
  </>
}

function saveButton(): HTMLElement {
  return screen.getByRole('button', { name: 'Save finding' })
}

async function confirmRemoval(user: UserEvent, key: string): Promise<void> {
  await user.click(await screen.findByRole('button', { name: `Remove retired value ${key}` }))
  const confirmation = await findRemovalConfirmation()
  await user.click(within(confirmation).getByRole('button', { name: 'Remove' }))
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Keep it' })).not.toBeInTheDocument())
}

describe('FindingDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders the default fields and saves only the filled values', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    renderDialog({ onSave })

    const severity = screen.getByRole('combobox', { name: 'Severity' })
    expect(severity).toHaveValue('Moderate')
    expect(severity).toBeRequired()
    await user.click(severity)
    expect(within(await screen.findByRole('listbox')).getAllByRole('option').map(option => option.textContent))
      .toEqual(['Critical', 'Important', 'Moderate', 'Low', 'Informational', 'Other'])
    await user.click(screen.getByRole('option', { name: 'Moderate' }))

    const harmType = screen.getByRole('combobox', { name: 'Harm-type' })
    expect(harmType).not.toBeRequired()
    await user.type(harmType, 'mal')
    expect(within(await screen.findByRole('listbox')).getAllByRole('option').map(option => option.textContent))
      .toEqual(['Not set', 'Malware', 'Other'])
    await user.click(screen.getByRole('option', { name: 'Not set' }))
    expect(harmType).toHaveValue('')

    const description = screen.getByRole('textbox', { name: 'Description' })
    expect(description).not.toBeRequired()
    expect(description).toHaveAccessibleDescription('Optional')
    expect(screen.queryByRole('heading', { level: 3 })).not.toBeInTheDocument()

    expect(saveButton()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Human assessment')
    await user.type(description, 'Notes')
    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: 'Human assessment', severity: 'moderate', field_values: { description: 'Notes' },
    }, ATTEMPT))
  })

  it('renders sections, hints and required markers from the fields', () => {
    renderDialog({ fields: CONTOSO_FINDING_FIELDS })

    expect(screen.getAllByRole('heading', { level: 3 }).map(heading => heading.textContent))
      .toEqual(['Classification', 'Reproduction', 'Tracking'])
    const steps = screen.getByRole('textbox', { name: 'Steps to reproduce' })
    expect(steps).toHaveAccessibleDescription('List each prompt you sent to the Contoso assistant, in order.')
    expect(steps).toBeRequired()
    expect(screen.getByRole('combobox', { name: 'Severity' })).toBeRequired()
    expect(screen.getByRole('combobox', { name: 'Severity' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Category' })).toBeRequired()
    expect(screen.getByRole('combobox', { name: 'Technique' })).not.toBeRequired()
    expect(screen.getByRole('textbox', { name: 'Owner' })).not.toBeRequired()

    const reproduction = screen.getByRole('heading', { level: 3, name: 'Reproduction' })
    const technique = screen.getByRole('combobox', { name: 'Technique' })
    const category = screen.getByRole('combobox', { name: 'Category' })
    expect(reproduction.compareDocumentPosition(technique) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(reproduction.compareDocumentPosition(category) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
  })

  it('disables save until every required value is filled', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    renderDialog({ fields: CONTOSO_FINDING_FIELDS, onSave })

    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Contoso prompt leak')
    expect(saveButton()).toBeDisabled()
    await pick(user, 'Severity', 'Sev 1')
    expect(saveButton()).toBeDisabled()
    await pick(user, 'Severity rationale', 'Customer impact')
    expect(saveButton()).toBeDisabled()
    await pick(user, 'Category', 'Model security')
    expect(saveButton()).toBeDisabled()
    await pick(user, 'Subcategory', 'System prompt leak')
    expect(saveButton()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Steps to reproduce' }), CONTOSO_STEPS)
    expect(saveButton()).toBeEnabled()
    await user.clear(screen.getByRole('textbox', { name: 'Title' }))
    expect(saveButton()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Contoso prompt leak')

    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: 'Contoso prompt leak', severity: 'sev1', field_values: CONTOSO_CURRENT_VALUES,
    }, ATTEMPT))
  })

  it('disables save while a chosen Other has blank text', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    renderDialog({ onSave })
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Custom classifications')

    await pick(user, 'Harm-type', 'Other')
    expect(saveButton()).toBeDisabled()
    fireEvent.submit(saveButton().closest('form') as HTMLFormElement)
    expect(onSave).not.toHaveBeenCalled()
    await user.type(screen.getByRole('textbox', { name: 'Other Harm-type' }), 'Team harm')
    expect(saveButton()).toBeEnabled()

    await pick(user, 'Severity', 'Other')
    expect(saveButton()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Other Severity' }), 'Team severity')
    expect(saveButton()).toBeEnabled()
    await user.clear(screen.getByRole('textbox', { name: 'Other Harm-type' }))
    expect(saveButton()).toBeDisabled()
    await user.type(screen.getByRole('textbox', { name: 'Other Harm-type' }), 'Team harm')

    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: 'Custom classifications', severity: 'Team severity', field_values: { harm_type: 'Team harm' },
    }, ATTEMPT))
  })

  it('edits resend kept retired values unchanged', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    renderDialog({ fields: CONTOSO_FINDING_FIELDS, editing: true, initialValues: CONTOSO_EDIT, onSave })

    expect(screen.getByRole('heading', { name: 'Edit finding' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Severity' })).toHaveValue('Sev 1')
    expect(screen.getByRole('textbox', { name: 'Steps to reproduce' })).toHaveValue(CONTOSO_STEPS)
    const retired = screen.getByRole('group', { name: 'Retired fields · read-only' })
    expect(within(retired).getByText('description')).toBeInTheDocument()
    expect(within(retired).getByText('Recorded before Contoso configured its own fields.')).toBeInTheDocument()

    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: CONTOSO_EDIT.title, severity: 'sev1', field_values: CONTOSO_EDIT.field_values, remove_retired_keys: [],
    }, ATTEMPT))
  })

  it('keeps the fields it opened with when the page reloads them, and resends a returned value unchanged', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    const props = { editing: true, initialValues: CONTOSO_EDIT, onSave, onClose: jest.fn() }
    // A live apply brought `description` back after the dialog opened, and the page reloaded its fields.
    const reloaded: FindingFields = {
      ...CONTOSO_FINDING_FIELDS, fields: [...CONTOSO_FINDING_FIELDS.fields, DEFAULT_FINDING_FIELDS_FIXTURE.fields[1]],
    }
    const { rerender } = render(<TestWrapper><FindingDialog fields={CONTOSO_FINDING_FIELDS} {...props} /></TestWrapper>)
    rerender(<TestWrapper><FindingDialog fields={reloaded} {...props} /></TestWrapper>)

    const retired = screen.getByRole('group', { name: 'Retired fields · read-only' })
    expect(within(retired).getByText('description')).toBeInTheDocument()
    expect(within(retired).getByText('Recorded before Contoso configured its own fields.')).toBeInTheDocument()
    expect(screen.queryByRole('textbox', { name: 'Description' })).not.toBeInTheDocument()

    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: CONTOSO_EDIT.title, severity: 'sev1', field_values: CONTOSO_EDIT.field_values, remove_retired_keys: [],
    }, ATTEMPT))
  })

  it('a removed retired value is sent in remove_retired_keys, and Cancel restores it', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    render(<TestWrapper><EditHarness onSave={onSave} /></TestWrapper>)
    // This bare page has no other Tabster user, so it can stay aria-hidden after the dialog closes; keep the opener.
    const edit = screen.getByRole('button', { name: 'Edit Contoso finding' })
    await user.click(edit)

    await confirmRemoval(user, 'description')
    expect(await screen.findByText('1 retired value is removed when you save.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Remove retired value description' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(onSave).not.toHaveBeenCalled()

    await user.click(edit)
    expect(await screen.findByRole('button', { name: 'Remove retired value description' })).toBeInTheDocument()
    expect(screen.queryByText(/removed when you save/)).not.toBeInTheDocument()
    await confirmRemoval(user, 'description')
    await user.click(await screen.findByRole('button', { name: 'Save finding' }))
    await waitFor(() => expect(onSave).toHaveBeenCalledWith({
      title: CONTOSO_EDIT.title, severity: 'sev1', field_values: CONTOSO_CURRENT_VALUES,
      remove_retired_keys: ['description'],
    }, ATTEMPT))
    // The backend rejects a key that is both sent and listed for removal.
    expect((onSave.mock.calls[0] as [FindingDraft])[0].field_values).not.toHaveProperty('description')
  })

  it('Escape closes only the removal confirmation', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    renderDialog({ fields: CONTOSO_FINDING_FIELDS, editing: true, initialValues: CONTOSO_EDIT, onClose })

    await user.click(screen.getByRole('button', { name: 'Remove retired value description' }))
    await findRemovalConfirmation()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Keep it' })).not.toBeInTheDocument())
    expect(onClose).not.toHaveBeenCalled()
    expect(await screen.findByRole('heading', { name: 'Edit finding' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Remove retired value description' })).toBeInTheDocument()
  })

  it('prevents duplicate submissions and reuses the attempt ID after a failure', async () => {
    const user = userEvent.setup()
    const pending = deferred<void>()
    const onSave = jest.fn()
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error('Response lost'))
      .mockResolvedValue(undefined)
    renderDialog({ onSave })
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Once')
    await user.click(saveButton())

    const saving = screen.getByRole('button', { name: 'Saving…' })
    expect(saving).toHaveAttribute('aria-disabled', 'true')
    await user.click(saving)
    fireEvent.submit(saving.closest('form') as HTMLFormElement)
    expect(onSave).toHaveBeenCalledTimes(1)

    await act(async () => { pending.reject(new Error('Response lost')) })
    expect(await screen.findByText('Response lost')).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Title' })).toHaveValue('Once')
    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(2))
    await user.type(await screen.findByRole('textbox', { name: 'Title' }), ' edited')
    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(3))

    const [first, retry, edited] = onSave.mock.calls.map(([, attempt]) => attempt.id)
    expect(retry).toBe(first)
    expect(edited).not.toBe(first)
  })

  it('shows a rejected Other text in the error message bar and keeps the draft', async () => {
    const user = userEvent.setup()
    const detail = 'Severity \'Critical\' matches the option \'Critical\' (key \'critical\'). Choose that option instead.'
    const onSave = jest.fn().mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail } } })
    renderDialog({ onSave })
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'Typed severity')
    await pick(user, 'Severity', 'Other')
    await user.type(screen.getByRole('textbox', { name: 'Other Severity' }), 'Critical')
    await user.click(saveButton())

    expect(await screen.findByText(detail)).toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Other Severity' })).toHaveValue('Critical')
    expect(saveButton()).toBeEnabled()
  })

  it('arms the unsaved changes guard once the draft differs from how it opened', async () => {
    const user = userEvent.setup()
    const events: Event[] = []
    const listener = (event: Event): void => { events.push(event) }
    window.addEventListener('beforeunload', listener)
    renderDialog()

    // The prevented flag is read after the dispatch, when every listener has run.
    fireEvent(window, new Event('beforeunload', { cancelable: true }))
    expect(events[0].defaultPrevented).toBe(false)
    await pick(user, 'Severity', 'Critical')
    fireEvent(window, new Event('beforeunload', { cancelable: true }))
    expect(events[1].defaultPrevented).toBe(true)
    await pick(user, 'Severity', 'Moderate')
    fireEvent(window, new Event('beforeunload', { cancelable: true }))
    expect(events[2].defaultPrevented).toBe(false)

    window.removeEventListener('beforeunload', listener)
  })

  it('offers an optional evidence attack only when asked and sends it trimmed', async () => {
    const user = userEvent.setup()
    const onSave = jest.fn().mockResolvedValue(undefined)
    renderDialog({ evidenceField: true, onSave })
    await user.type(screen.getByRole('textbox', { name: 'Title' }), 'With evidence')
    await user.type(screen.getByRole('textbox', { name: 'Evidence attack ID' }), '  attack-1 ')
    await user.click(saveButton())
    await waitFor(() => expect(onSave).toHaveBeenCalledWith(expect.objectContaining({ title: 'With evidence' }), {
      id: expect.any(String), attackResultId: 'attack-1',
    }))
  })
})
