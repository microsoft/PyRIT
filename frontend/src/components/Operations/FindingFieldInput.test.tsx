import React, { useState } from 'react'
import { render, screen } from '@testing-library/react'
import userEvent, { type UserEvent } from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { pick } from '@/test-utils/findingDialog'
import { CONTOSO_FINDING_FIELDS, contosoField, option } from '@/test-utils/findingFieldsFixtures'
import type { FindingField } from '@/types'

import FindingFieldInput from './FindingFieldInput'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const UNMATCHED_HINT = 'Not a current option. It stays unless you change it.'

const CATEGORY = contosoField('category')
const TECHNIQUE = contosoField('technique')
const STEPS = contosoField('steps')
const OWNER = contosoField('owner')

// Both surfaces list a `plugins` component, but only the web chat's plugins include email.
const SURFACE: FindingField = {
  ...CATEGORY,
  key: 'surface',
  label: 'Surface',
  levels: [['component', 'Component'], ['capability', 'Capability']],
  options: [
    option('web_chat', 'Web chat', [
      option('plugins', 'Plugins', [option('calendar', 'Calendar'), option('email', 'Email')]),
    ]),
    option('mobile_app', 'Mobile app', [option('plugins', 'Plugins', [option('calendar', 'Calendar')])]),
  ],
}

// Outage is a leaf option, so it leaves Impact detail nothing to offer.
const IMPACT: FindingField = {
  ...CATEGORY,
  key: 'impact',
  label: 'Impact',
  levels: [['impact_detail', 'Impact detail']],
  options: [
    option('data_exposure', 'Data exposure', [option('customer_records', 'Customer records')]),
    option('outage', 'Outage'),
  ],
}

const TACTIC: FindingField = { ...TECHNIQUE, key: 'tactic', label: 'Tactic' }

interface FieldHarnessProps {
  field: FindingField
  initialValues: Record<string, string>
  onChange: (changes: Record<string, string>) => void
  onOtherBlankChange?: (blank: boolean) => void
}

// Holds the draft as the finding dialog does, so a test can follow several changes.
function FieldHarness({ field, initialValues, onChange, onOtherBlankChange }: FieldHarnessProps) {
  const [values, setValues] = useState(initialValues)
  return (
    <FindingFieldInput field={field} values={values} onOtherBlankChange={onOtherBlankChange}
      onChange={(changes: Record<string, string>) => {
        onChange(changes)
        setValues((previous) => ({ ...previous, ...changes }))
      }} />
  )
}

async function openPicker(user: UserEvent, label: string): Promise<void> {
  await user.click(screen.getByRole('combobox', { name: label }))
  await screen.findByRole('listbox')
}

function optionNames(): string[] {
  return screen.getAllByRole('option').map((element: HTMLElement) => element.textContent ?? '')
}

describe('FindingFieldInput', () => {
  const onChange = jest.fn()

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('renders a text or multiline field with its label, hint and required marker', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FindingFieldInput field={STEPS} values={{ steps: '1. Open a chat' }} onChange={onChange} />
      <FindingFieldInput field={OWNER} values={{}} onChange={onChange} />
    </TestWrapper>)

    const steps = screen.getByRole('textbox', { name: 'Steps to reproduce' })
    expect(steps).toBeInstanceOf(HTMLTextAreaElement)
    expect(steps).toHaveValue('1. Open a chat')
    expect(steps).toBeRequired()
    expect(steps).toHaveAccessibleDescription('List each prompt you sent to the Contoso assistant, in order.')
    expect(screen.getByText('List each prompt you sent to the Contoso assistant, in order.')).toBeInTheDocument()

    const owner = screen.getByRole('textbox', { name: 'Owner' })
    expect(owner).toBeInstanceOf(HTMLInputElement)
    expect(owner).toHaveValue('')
    expect(owner).not.toBeRequired()
    expect(owner).not.toHaveAccessibleDescription()

    await user.type(steps, '.')
    expect(onChange).toHaveBeenLastCalledWith({ steps: '1. Open a chat.' })
    await user.type(owner, 'C')
    expect(onChange).toHaveBeenLastCalledWith({ owner: 'C' })
  })

  it('picks a cascade path level by level and disables a level until its parent has a value', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><FieldHarness field={CATEGORY} initialValues={{}} onChange={onChange} /></TestWrapper>)

    expect(screen.getByRole('combobox', { name: 'Category' })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).not.toBeRequired()

    await pick(user, 'Category', 'Model security')
    expect(onChange).toHaveBeenLastCalledWith({ category: 'model_security' })
    expect(screen.getByRole('combobox', { name: 'Category' })).toHaveValue('Model security')
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).toBeEnabled()
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).toBeRequired()

    await openPicker(user, 'Subcategory')
    expect(optionNames()).toEqual(['System prompt leak', 'Tool misuse'])
    await user.click(screen.getByRole('option', { name: 'Tool misuse' }))
    expect(onChange).toHaveBeenLastCalledWith({ subcategory: 'tool_misuse' })
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).toHaveValue('Tool misuse')
  })

  it('clears a child that is not valid under the new parent and keeps one that is', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={CATEGORY} initialValues={{ category: 'model_security', subcategory: 'system_prompt_leak' }}
        onChange={onChange} />
      <FieldHarness field={SURFACE} initialValues={{ surface: 'web_chat', component: 'plugins', capability: 'email' }}
        onChange={onChange} />
    </TestWrapper>)

    await pick(user, 'Category', 'Content safety')
    expect(onChange).toHaveBeenLastCalledWith({ category: 'content_safety', subcategory: '' })
    expect(screen.getByRole('combobox', { name: 'Subcategory' })).toHaveValue('')

    await pick(user, 'Surface', 'Mobile app')
    expect(onChange).toHaveBeenLastCalledWith({ surface: 'mobile_app', capability: '' })
    expect(screen.getByRole('combobox', { name: 'Component' })).toHaveValue('Plugins')
    expect(screen.getByRole('combobox', { name: 'Capability' })).toHaveValue('')

    await pick(user, 'Capability', 'Calendar')
    await pick(user, 'Surface', 'Web chat')
    expect(onChange).toHaveBeenLastCalledWith({ surface: 'web_chat' })
    expect(screen.getByRole('combobox', { name: 'Component' })).toHaveValue('Plugins')
    expect(screen.getByRole('combobox', { name: 'Capability' })).toHaveValue('Calendar')
  })

  it('offers Other on a single-level field and reports the typed text as the value', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={TECHNIQUE} initialValues={{}} onChange={onChange} />
      <FindingFieldInput field={{ ...CATEGORY, allow_other: true }} values={{}} onChange={onChange} />
    </TestWrapper>)

    await openPicker(user, 'Category')
    expect(screen.queryByRole('option', { name: 'Other' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')

    await openPicker(user, 'Technique')
    expect(optionNames()).toEqual(['Not set', 'Prompt injection', 'Role play', 'Other'])
    await user.click(screen.getByRole('option', { name: 'Other' }))
    expect(onChange).toHaveBeenLastCalledWith({ technique: '' })
    expect(screen.getByRole('combobox', { name: 'Technique' })).toHaveValue('Other')

    const otherText = screen.getByRole('textbox', { name: 'Other Technique' })
    expect(otherText).toBeRequired()
    await user.type(otherText, 'Many-shot')
    expect(onChange).toHaveBeenLastCalledWith({ technique: 'Many-shot' })
    expect(otherText).toHaveValue('Many-shot')
    expect(screen.getByRole('combobox', { name: 'Technique' })).toHaveValue('Other')

    await pick(user, 'Technique', 'Role play')
    expect(onChange).toHaveBeenLastCalledWith({ technique: 'role_play' })
    expect(screen.queryByRole('textbox', { name: 'Other Technique' })).not.toBeInTheDocument()
  })

  it('opens a stored Other value with its text', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={TECHNIQUE} initialValues={{ technique: 'Model extraction' }} onChange={onChange} />
    </TestWrapper>)

    expect(screen.getByRole('combobox', { name: 'Technique' })).toHaveValue('Other')
    expect(screen.getByRole('textbox', { name: 'Other Technique' })).toHaveValue('Model extraction')
    expect(screen.queryByText(UNMATCHED_HINT)).not.toBeInTheDocument()

    await openPicker(user, 'Technique')
    await user.click(screen.getByRole('option', { name: 'Other', selected: true }))
    await user.tab()
    expect(onChange).not.toHaveBeenCalled()
    expect(screen.getByRole('combobox', { name: 'Technique' })).toHaveValue('Other')
    expect(screen.getByRole('textbox', { name: 'Other Technique' })).toHaveValue('Model extraction')
  })

  it('shows a stored value that is no longer an option and keeps it until changed', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={CATEGORY} initialValues={{ category: 'model_security', subcategory: 'Training data leak' }}
        onChange={onChange} />
      <FindingFieldInput field={CONTOSO_FINDING_FIELDS.severity} values={{ severity: 'Sev 0' }} onChange={onChange} />
    </TestWrapper>)

    const severity = screen.getByRole('combobox', { name: 'Severity' })
    expect(severity).toHaveValue('Sev 0')
    expect(severity).toHaveAccessibleDescription(UNMATCHED_HINT)

    const subcategory = screen.getByRole('combobox', { name: 'Subcategory' })
    expect(subcategory).toHaveValue('Training data leak')
    expect(subcategory).toHaveAccessibleDescription(UNMATCHED_HINT)
    await openPicker(user, 'Subcategory')
    expect(optionNames()).toEqual(['Training data leak', 'System prompt leak', 'Tool misuse'])
    await user.click(screen.getByRole('option', { name: 'Training data leak', selected: true }))
    await user.tab()
    expect(onChange).not.toHaveBeenCalled()
    expect(subcategory).toHaveValue('Training data leak')

    await pick(user, 'Subcategory', 'Tool misuse')
    expect(onChange).toHaveBeenLastCalledWith({ subcategory: 'tool_misuse' })
    expect(subcategory).toHaveValue('Tool misuse')
    expect(subcategory).not.toHaveAccessibleDescription()
    await openPicker(user, 'Subcategory')
    expect(optionNames()).toEqual(['System prompt leak', 'Tool misuse'])
  })

  it('offers Not set only on optional fields', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={TECHNIQUE} initialValues={{ technique: 'role_play' }} onChange={onChange} />
      <FindingFieldInput field={CATEGORY} values={{ category: 'model_security' }} onChange={onChange} />
      <FindingFieldInput field={{ ...SURFACE, required: false }} values={{ surface: 'web_chat' }} onChange={onChange} />
    </TestWrapper>)

    await openPicker(user, 'Category')
    expect(screen.queryByRole('option', { name: 'Not set' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')

    await openPicker(user, 'Surface')
    expect(screen.getByRole('option', { name: 'Not set' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await openPicker(user, 'Component')
    expect(screen.queryByRole('option', { name: 'Not set' })).not.toBeInTheDocument()
    await user.keyboard('{Escape}')

    await pick(user, 'Technique', 'Not set')
    expect(onChange).toHaveBeenLastCalledWith({ technique: '' })
    expect(screen.getByRole('combobox', { name: 'Technique' })).toHaveValue('')
  })

  it('filters options by the typed text', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={CATEGORY} initialValues={{}} onChange={onChange} />
      <FieldHarness field={TECHNIQUE} initialValues={{}} onChange={onChange} />
    </TestWrapper>)

    const category = screen.getByRole('combobox', { name: 'Category' })
    await user.type(category, 'safety')
    expect(optionNames()).toEqual(['Content safety'])
    await user.clear(category)
    await user.type(category, 'zzz')
    expect(screen.getByRole('option', { name: 'No matching options' })).toHaveAttribute('aria-disabled', 'true')
    await user.keyboard('{Escape}')

    await user.type(screen.getByRole('combobox', { name: 'Technique' }), 'zzz')
    expect(optionNames()).toEqual(['Not set', 'No matching options', 'Other'])
    expect(onChange).not.toHaveBeenCalled()

    await user.click(screen.getByRole('option', { name: 'Other' }))
    expect(onChange).toHaveBeenLastCalledWith({ technique: '' })
    expect(screen.getByRole('textbox', { name: 'Other Technique' })).toHaveValue('')
  })

  it('reports whether the Other text is blank', async () => {
    const user = userEvent.setup()
    const onOtherBlankChange = jest.fn()
    const onStoredOtherBlankChange = jest.fn()
    render(<TestWrapper>
      <FieldHarness field={TECHNIQUE} initialValues={{}} onChange={onChange} onOtherBlankChange={onOtherBlankChange} />
      <FieldHarness field={TACTIC} initialValues={{ tactic: 'Model extraction' }} onChange={onChange}
        onOtherBlankChange={onStoredOtherBlankChange} />
    </TestWrapper>)
    expect(onOtherBlankChange).not.toHaveBeenCalled()
    expect(onStoredOtherBlankChange).not.toHaveBeenCalled()

    await pick(user, 'Technique', 'Other')
    expect(onOtherBlankChange).toHaveBeenLastCalledWith(true)
    const otherText = screen.getByRole('textbox', { name: 'Other Technique' })
    await user.type(otherText, 'Many-shot')
    expect(onOtherBlankChange).toHaveBeenLastCalledWith(false)
    await user.clear(otherText)
    expect(onOtherBlankChange).toHaveBeenLastCalledWith(true)
    await pick(user, 'Technique', 'Role play')
    expect(onOtherBlankChange).toHaveBeenLastCalledWith(false)

    await user.clear(screen.getByRole('textbox', { name: 'Other Tactic' }))
    expect(onStoredOtherBlankChange).toHaveBeenCalledTimes(1)
    expect(onStoredOtherBlankChange).toHaveBeenLastCalledWith(true)
  })

  it('disables a lower level with nothing to offer and leaves it unmarked', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={IMPACT} initialValues={{ impact: 'Service degradation' }} onChange={onChange} />
    </TestWrapper>)
    const detail = screen.getByRole('combobox', { name: 'Impact detail' })
    expect(screen.getByRole('combobox', { name: 'Impact' })).toHaveValue('Service degradation')
    expect(detail).toBeDisabled()
    expect(detail).not.toBeRequired()

    await pick(user, 'Impact', 'Outage')
    expect(onChange).toHaveBeenLastCalledWith({ impact: 'outage' })
    expect(detail).toBeDisabled()
    expect(detail).not.toBeRequired()

    await pick(user, 'Impact', 'Data exposure')
    expect(detail).toBeEnabled()
    expect(detail).toBeRequired()
  })

  it('disables every control while disabled', () => {
    render(<TestWrapper>
      <FindingFieldInput field={STEPS} values={{}} disabled onChange={onChange} />
      <FindingFieldInput field={OWNER} values={{}} disabled onChange={onChange} />
      <FindingFieldInput field={CATEGORY} values={{ category: 'model_security' }} disabled onChange={onChange} />
      <FindingFieldInput field={TECHNIQUE} values={{ technique: 'Model extraction' }} disabled onChange={onChange} />
    </TestWrapper>)
    const comboboxes = screen.getAllByRole('combobox')
    const textboxes = screen.getAllByRole('textbox')
    expect(comboboxes).toHaveLength(3)
    expect(textboxes).toHaveLength(3)
    for (const control of [...comboboxes, ...textboxes]) expect(control).toBeDisabled()
  })

  it('clears every lower level when an optional cascade is set to Not set', async () => {
    const user = userEvent.setup()
    render(<TestWrapper>
      <FieldHarness field={{ ...SURFACE, required: false }}
        initialValues={{ surface: 'web_chat', component: 'plugins', capability: 'email' }} onChange={onChange} />
    </TestWrapper>)

    await pick(user, 'Surface', 'Not set')
    expect(onChange).toHaveBeenLastCalledWith({ surface: '', component: '', capability: '' })
    expect(screen.getByRole('combobox', { name: 'Surface' })).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Component' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Capability' })).toBeDisabled()
  })

  it('ignores inherited members for a field keyed constructor and an option keyed toString', async () => {
    const user = userEvent.setup()
    const field: FindingField = {
      ...TECHNIQUE, key: 'constructor', label: 'Constructor', options: [option('toString', 'To string')],
    }
    render(<TestWrapper>
      <FieldHarness field={field} initialValues={{}} onChange={onChange} />
      <FindingFieldInput field={{ ...OWNER, key: 'constructor' }} values={{}} onChange={onChange} />
    </TestWrapper>)

    expect(screen.getByRole('combobox', { name: 'Constructor' })).toHaveValue('')
    expect(screen.queryByRole('textbox', { name: 'Other Constructor' })).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Owner' })).toHaveValue('')
    await pick(user, 'Constructor', 'To string')
    expect(onChange).toHaveBeenLastCalledWith({ constructor: 'toString' })
    expect(screen.getByRole('combobox', { name: 'Constructor' })).toHaveValue('To string')
  })
})
