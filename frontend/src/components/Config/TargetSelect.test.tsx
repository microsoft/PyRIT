import { useState } from 'react'

import {
  Button,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  FluentProvider,
  webLightTheme,
} from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { TargetInstance } from '@/types'

import TargetSelect from './TargetSelect'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

const targets = [
  makeTarget({ target_registry_name: 'target-a', model_name: 'gpt-4o' }),
  makeTarget({ target_registry_name: 'target-b', model_name: null }),
]

describe('TargetSelect', () => {
  beforeEach(() => jest.clearAllMocks())

  it('should display registry names and models and allow selecting and clearing', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    function Selection() {
      const [value, setValue] = useState('')
      return (
        <TargetSelect
          targets={targets}
          value={value}
          label="Objective target"
          onChange={(target: TargetInstance | null) => {
            setValue(target?.target_registry_name ?? '')
            onChange(target)
          }}
        />
      )
    }
    render(<TestWrapper><Selection /></TestWrapper>)
    const select = screen.getByRole('combobox', { name: 'Objective target' })
    expect(select).toHaveValue('')
    expect(screen.getByRole('option', { name: 'target-a (gpt-4o)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'target-b' })).toBeInTheDocument()
    await user.selectOptions(select, 'target-b')
    expect(select).toHaveValue('target-b')
    expect(onChange).toHaveBeenLastCalledWith(targets[1])
    await user.selectOptions(select, '')
    expect(select).toHaveValue('')
    expect(onChange).toHaveBeenLastCalledWith(null)
  })

  it('should list targets that need your key without letting you choose them', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const needsKey = makeTarget({
      target_registry_name: 'own-a', model_name: 'gpt-4o',
      requires_caller_credential: true, caller_credential_configured: false,
    })
    const keySet = makeTarget({
      target_registry_name: 'own-b', requires_caller_credential: true, caller_credential_configured: true,
    })
    render(
      <TestWrapper>
        <TargetSelect targets={[...targets, needsKey, keySet]} value="" label="Adversarial target" onChange={onChange} />
      </TestWrapper>,
    )
    const select = screen.getByRole('combobox', { name: 'Adversarial target' })

    expect(screen.getByRole('option', { name: 'own-a (gpt-4o) · needs your key' })).toBeDisabled()
    expect(screen.getByRole('option', { name: 'own-b · yours' })).toBeEnabled()
    expect(screen.getByRole('option', { name: 'target-a (gpt-4o)' })).toBeEnabled()
    await user.selectOptions(select, 'own-a')
    expect(onChange).not.toHaveBeenCalled()
    await user.selectOptions(select, 'own-b')
    expect(onChange).toHaveBeenCalledWith(keySet)
  })

  it('should retain the blank placeholder for an empty registry and respect disabled', () => {
    render(
      <TestWrapper>
        <TargetSelect
          targets={[]}
          value=""
          onChange={jest.fn()}
          label="Adversarial fallback target"
          placeholder="Use server default"
          disabled
        />
      </TestWrapper>,
    )
    expect(screen.getByRole('combobox', { name: 'Adversarial fallback target' })).toBeDisabled()
    expect(screen.getByRole('option', { name: 'Use server default' })).toBeInTheDocument()
  })

  it('should submit the selected destination inside a dialog', async () => {
    const user = userEvent.setup()
    const onSubmit = jest.fn()
    function BranchDialog() {
      const [target, setTarget] = useState<TargetInstance | null>(targets[0])
      return (
        <Dialog open>
          <DialogSurface>
            <DialogBody>
              <DialogTitle>Continue in a new attack</DialogTitle>
              <DialogContent>
                <TargetSelect
                  targets={targets}
                  value={target?.target_registry_name ?? ''}
                  label="Destination target"
                  onChange={setTarget}
                />
              </DialogContent>
              <DialogActions>
                <Button onClick={() => { onSubmit(target) }}>Create attack</Button>
              </DialogActions>
            </DialogBody>
          </DialogSurface>
        </Dialog>
      )
    }
    render(<TestWrapper><BranchDialog /></TestWrapper>)
    const select = await screen.findByRole('combobox', { name: 'Destination target' })
    expect(select).toHaveValue('target-a')
    await user.selectOptions(select, 'target-b')
    expect(select).toHaveValue('target-b')
    await user.click(screen.getByRole('button', { name: 'Create attack' }))
    expect(onSubmit).toHaveBeenCalledWith(targets[1])
  })
})
