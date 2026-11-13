import { useState } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { RegistryReferenceOption } from '@/types'

import ReferenceField from './ReferenceField'

const OPTIONS: RegistryReferenceOption[] = [
  { name: 'base64', type: 'Base64Converter' },
  { name: 'rot13', type: 'ROT13Converter' },
  { name: 'leet', type: 'LeetspeakConverter' },
]

const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

interface ControlledProps {
  initial: string | string[]
  multiple?: boolean
  disabled?: boolean
  onChange?: (value: string | string[]) => void
}

function ControlledReferenceField({ initial, multiple, disabled, onChange }: ControlledProps) {
  const [value, setValue] = useState(initial)
  return (
    <ReferenceField
      label="Converters"
      hint="Applied in order"
      options={OPTIONS}
      value={value}
      multiple={multiple}
      disabled={disabled}
      onChange={(next) => {
        setValue(next)
        onChange?.(next)
      }}
    />
  )
}

function renderField(props: ControlledProps) {
  const onChange = props.onChange ?? jest.fn()
  render(<TestWrapper><ControlledReferenceField {...props} onChange={onChange} /></TestWrapper>)
  return { onChange, user: userEvent.setup() }
}

/** The entry rows in display order, read from their visible "N. name" text. */
function entries(): string[] {
  return screen.queryAllByText(/^\d+\. /).map((node) => node.textContent ?? '')
}

function rowButtons(position: number) {
  const row = screen.getByText(new RegExp(`^${position}\\. `)).parentElement as HTMLElement
  return {
    up: within(row).getByRole('button', { name: `Move Converters ${position} up` }),
    down: within(row).getByRole('button', { name: `Move Converters ${position} down` }),
    remove: within(row).getByRole('button', { name: `Remove Converters ${position}` }),
  }
}

describe('ReferenceField', () => {
  describe('single reference', () => {
    it('shows the registry options with their types and a not-set default', () => {
      renderField({ initial: '' })

      const select = screen.getByRole('combobox', { name: 'Converters' })
      expect(within(select).getAllByRole('option').map((option) => option.textContent)).toEqual([
        'Use default / not set',
        'base64 (Base64Converter)',
        'rot13 (ROT13Converter)',
        'leet (LeetspeakConverter)',
      ])
      expect(select).toHaveValue('')
      expect(screen.getByText('Applied in order')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: /Add to/ })).not.toBeInTheDocument()
    })

    it('reports the chosen name, and an empty string when reset to the default', async () => {
      const { onChange, user } = renderField({ initial: '' })
      const select = screen.getByRole('combobox', { name: 'Converters' })

      await user.selectOptions(select, 'rot13')
      expect(onChange).toHaveBeenLastCalledWith('rot13')
      expect(select).toHaveValue('rot13')

      await user.selectOptions(select, '')
      expect(onChange).toHaveBeenLastCalledWith('')
      expect(select).toHaveValue('')
    })

    it('shows the not-set option when given a list value', () => {
      renderField({ initial: ['base64'] })

      expect(screen.getByRole('combobox', { name: 'Converters' })).toHaveValue('')
    })
  })

  describe('multiple references', () => {
    it('adds the pending selection only once one is chosen, and allows repeats', async () => {
      const { onChange, user } = renderField({ initial: [], multiple: true })
      const select = screen.getByRole('combobox', { name: 'Converters' })
      const add = screen.getByRole('button', { name: 'Add to Converters' })

      expect(within(select).getByRole('option', { name: 'Select an instance to add' })).toBeInTheDocument()
      expect(add).toBeDisabled()

      await user.selectOptions(select, 'base64')
      expect(onChange).not.toHaveBeenCalled()
      expect(add).toBeEnabled()

      await user.click(add)
      expect(onChange).toHaveBeenLastCalledWith(['base64'])

      await user.click(add)
      expect(onChange).toHaveBeenLastCalledWith(['base64', 'base64'])

      await user.selectOptions(select, 'rot13')
      await user.click(add)
      expect(onChange).toHaveBeenLastCalledWith(['base64', 'base64', 'rot13'])
      expect(entries()).toEqual(['1. base64', '2. base64', '3. rot13'])
    })

    it('moves an entry up and down', async () => {
      const { onChange, user } = renderField({ initial: ['base64', 'rot13', 'leet'], multiple: true })

      await user.click(rowButtons(3).up)
      expect(onChange).toHaveBeenLastCalledWith(['base64', 'leet', 'rot13'])
      expect(entries()).toEqual(['1. base64', '2. leet', '3. rot13'])

      await user.click(rowButtons(1).down)
      expect(onChange).toHaveBeenLastCalledWith(['leet', 'base64', 'rot13'])
      expect(entries()).toEqual(['1. leet', '2. base64', '3. rot13'])
    })

    it('disables Up on the first entry and Down on the last', () => {
      renderField({ initial: ['base64', 'rot13', 'leet'], multiple: true })

      expect(rowButtons(1).up).toBeDisabled()
      expect(rowButtons(1).down).toBeEnabled()
      expect(rowButtons(2).up).toBeEnabled()
      expect(rowButtons(2).down).toBeEnabled()
      expect(rowButtons(3).up).toBeEnabled()
      expect(rowButtons(3).down).toBeDisabled()
    })

    it('disables both moves when there is a single entry', () => {
      renderField({ initial: ['base64'], multiple: true })

      expect(rowButtons(1).up).toBeDisabled()
      expect(rowButtons(1).down).toBeDisabled()
      expect(rowButtons(1).remove).toBeEnabled()
    })

    it('removes the entry at its own position when names repeat', async () => {
      const { onChange, user } = renderField({ initial: ['base64', 'rot13', 'base64'], multiple: true })

      await user.click(rowButtons(3).remove)
      expect(onChange).toHaveBeenLastCalledWith(['base64', 'rot13'])
      expect(entries()).toEqual(['1. base64', '2. rot13'])

      await user.click(rowButtons(1).remove)
      expect(onChange).toHaveBeenLastCalledWith(['rot13'])
      expect(entries()).toEqual(['1. rot13'])

      await user.click(rowButtons(1).remove)
      expect(onChange).toHaveBeenLastCalledWith([])
      expect(entries()).toEqual([])
    })

    it('keeps ordering and removal correct after entries were added and moved', async () => {
      const { onChange, user } = renderField({ initial: ['base64'], multiple: true })

      await user.selectOptions(screen.getByRole('combobox', { name: 'Converters' }), 'leet')
      await user.click(screen.getByRole('button', { name: 'Add to Converters' }))
      await user.click(rowButtons(2).up)
      expect(entries()).toEqual(['1. leet', '2. base64'])

      await user.click(rowButtons(2).remove)
      expect(onChange).toHaveBeenLastCalledWith(['leet'])
      expect(entries()).toEqual(['1. leet'])
    })

    it('disables every control when disabled', async () => {
      const { onChange, user } = renderField({ initial: ['base64', 'rot13'], multiple: true, disabled: true })

      expect(screen.getByRole('combobox', { name: 'Converters' })).toBeDisabled()
      expect(screen.getByRole('button', { name: 'Add to Converters' })).toBeDisabled()
      for (const position of [1, 2]) {
        const { up, down, remove } = rowButtons(position)
        expect(up).toBeDisabled()
        expect(down).toBeDisabled()
        expect(remove).toBeDisabled()
      }

      await user.click(rowButtons(1).remove)
      expect(onChange).not.toHaveBeenCalled()
      expect(entries()).toEqual(['1. base64', '2. rot13'])
    })
  })
})
