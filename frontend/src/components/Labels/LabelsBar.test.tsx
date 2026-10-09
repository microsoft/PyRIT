import { useState } from 'react'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import LabelsBar from './LabelsBar'
import { DEFAULT_GLOBAL_LABELS as FRESH_GLOBAL_LABELS } from './labelDefaults'
import { labelsApi, operationsApi } from '@/services/api'

// These regressions also exercise preferences saved before operations were persistent records.
const DEFAULT_GLOBAL_LABELS = { ...FRESH_GLOBAL_LABELS, operation: 'op_trash_panda' }

jest.mock('../../services/api', () => ({
  labelsApi: {
    getLabels: jest.fn(),
  },
  operationsApi: { list: jest.fn(), create: jest.fn() },
}))

const mockedLabelsApi = labelsApi as jest.Mocked<typeof labelsApi>

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('LabelsBar', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedLabelsApi.getLabels.mockImplementation(() => new Promise(() => {}))
    jest.mocked(operationsApi.list).mockImplementation(() => new Promise(() => {}))
  })

  it('should render legacy default labels', () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // The visible inline chips are the canonical render. The component
    // also has an aria-hidden "measure" row with mirrored chips used
    // purely to compute available width — query by data-testid so we
    // don't accidentally match the hidden mirror.
    expect(screen.getByTestId('edit-label-operator')).toHaveValue('roakey')
    expect(screen.getByTestId('edit-label-operation')).toHaveValue('op_trash_panda')
  })

  it('should show warning icon for dummy values', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    expect(screen.getByTestId('labels-warning')).toBeInTheDocument()
  })

  it('should keep the signed-in operator read-only in the ribbon and absent from the popover', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ operator: 'alice', operation: 'op_demo' }} onLabelsChange={onChange} operatorReadOnly />
      </TestWrapper>,
    )
    const operator = screen.getByRole('textbox', { name: 'Signed-in operator' })
    expect(operator).toHaveAttribute('readonly')
    await user.click(operator)
    expect(operator).toHaveValue('alice')
    await user.click(screen.getByTestId('labels-icon-btn'))
    expect(await screen.findByRole('heading', { name: 'Default Labels' })).toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-label-operator')).not.toBeInTheDocument()
    expect(screen.getByTestId('edit-label-operator')).toHaveAttribute('readonly')
    expect(onChange).not.toHaveBeenCalled()
  })

  it('should not show warning when values are customized', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ operator: 'alice', operation: 'my_test' }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('labels-warning')).not.toBeInTheDocument()
  })

  it.each([
    {
      labels: { ...DEFAULT_GLOBAL_LABELS },
      message: 'Set operator and operation in the bar. The current values are placeholders.',
    },
    {
      labels: { ...DEFAULT_GLOBAL_LABELS, operator: 'alice' },
      message: 'Set operation in the bar. The current value is a placeholder.',
    },
    {
      labels: { ...DEFAULT_GLOBAL_LABELS, operation: 'my_test' },
      message: 'Set operator in the bar. The current value is a placeholder.',
    },
  ])('should explain the warning: $message', async ({ labels, message }: {
    labels: Record<string, string>
    message: string
  }) => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <LabelsBar labels={labels} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: 'Show warnings' }))

    expect(await screen.findByRole('heading', { name: 'Warnings' })).toBeInTheDocument()
    expect(screen.getByText(message)).toBeInTheDocument()
  })

  it('should open and dismiss the warning with the keyboard', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    await user.tab()
    const warning = screen.getByRole('button', { name: 'Show warnings' })
    expect(warning).toHaveFocus()
    await user.keyboard('{Enter}')
    expect(await screen.findByRole('heading', { name: 'Warnings' })).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => {
      expect(screen.queryByRole('heading', { name: 'Warnings' })).not.toBeInTheDocument()
    })
    expect(warning).toHaveFocus()
  })

  it('should allow removing the operation but not the operator', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('remove-label-operator')).not.toBeInTheDocument()
    expect(screen.getByTestId('remove-label-operation')).toBeInTheDocument()
  })

  it('should allow removing custom labels', () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    const removeBtn = screen.getByTestId('remove-label-team')
    fireEvent.click(removeBtn)

    expect(onChange).toHaveBeenCalledWith({
      operator: 'roakey',
      operation: 'op_trash_panda',
    })
  })

  it('should describe what clicking a chip does, on the control you focus', async () => {
    // Fluent hangs the tooltip on whatever it wraps, so it has to wrap the
    // control that actually takes focus, not the pill around it.
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    expect(screen.getByRole('combobox', { name: 'Operation' })).toBeInTheDocument()
  })

  it('should keep the remove button out of the edit control', async () => {
    // A control that removes the label cannot sit inside the control that
    // edits it: screen readers flatten the inner one and it loses its name.
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    const edit = screen.getByTestId('label-team')
    const remove = screen.getByTestId('remove-label-team')

    expect(edit).toHaveAttribute('role', 'button')
    expect(edit).not.toContainElement(remove)
    expect(remove).toHaveAccessibleName('Remove team label')
    // Required labels have nothing to nest in the first place.
    expect(screen.getByRole('textbox', { name: 'Operator' })).toBeInTheDocument()
  })

  it('should start an edit when the chip is clicked beside the edit control', async () => {
    // Moving the click onto an inner control left the pill's own padding
    // showing a pointer and doing nothing, so the edges of a chip looked
    // clickable but were not.
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('label-team').parentElement as HTMLElement)

    expect(await screen.findByTestId('edit-label-team')).toBeInTheDocument()
  })

  it('should add a new label via popover', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    const valueInput = screen.getByPlaceholderText('value')

    fireEvent.change(keyInput, { target: { value: 'team' } })
    fireEvent.change(valueInput, { target: { value: 'red' } })
    fireEvent.click(screen.getByTestId('confirm-add-label'))

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GLOBAL_LABELS,
      team: 'red',
    })
  })

  it('should reject uppercase keys', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    const valueInput = screen.getByPlaceholderText('value')

    // The onChange handler auto-lowercases input, so 'Team' becomes 'team' and 'Red' becomes 'red'
    fireEvent.change(keyInput, { target: { value: 'Team' } })
    fireEvent.change(valueInput, { target: { value: 'Red' } })
    fireEvent.click(screen.getByTestId('confirm-add-label'))

    // Since auto-lowercase is applied, the label should be added with lowercase values
    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GLOBAL_LABELS,
      team: 'red',
    })
  })

  it('should reject duplicate keys', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    const valueInput = screen.getByPlaceholderText('value')

    fireEvent.change(keyInput, { target: { value: 'operator' } })
    fireEvent.change(valueInput, { target: { value: 'alice' } })
    fireEvent.click(screen.getByTestId('confirm-add-label'))

    expect(screen.getByText('Label key already exists')).toBeInTheDocument()
  })

  it.each(['operator', 'operation'])('should not add %s through the default labels form', async (key: string) => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{}} onLabelsChange={onChange} />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /^0 labels/ }))
    await user.type(screen.getByRole('textbox', { name: 'Label key' }), key)
    await user.type(screen.getByRole('textbox', { name: 'Label value' }), 'alice')
    await user.click(screen.getByRole('button', { name: 'Add', exact: true }))

    expect(screen.getByText('Set operator and operation in the bar')).toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('should allow editing a label value by clicking on it', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // Click on operator label to edit
    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })
  })

  it('should export correct default labels', () => {
    expect(DEFAULT_GLOBAL_LABELS).toEqual({
      operator: 'roakey',
      operation: 'op_trash_panda',
    })
  })

  it('should fetch existing labels on mount', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })
  })

  it('should handle getLabels failure gracefully', async () => {
    mockedLabelsApi.getLabels.mockRejectedValueOnce(new Error('Network error'))

    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    // Component should still render without errors
    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })
    expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
  })

  it('should reject empty key when adding a label', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    // Leave key empty, set value
    const valueInput = screen.getByPlaceholderText('value')
    fireEvent.change(valueInput, { target: { value: 'somevalue' } })
    fireEvent.click(screen.getByTestId('confirm-add-label'))

    expect(screen.getByText('Key is required')).toBeInTheDocument()
  })

  it('should reject empty value when adding a label', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    fireEvent.change(keyInput, { target: { value: 'mykey' } })
    // Leave value empty
    fireEvent.click(screen.getByTestId('confirm-add-label'))

    expect(screen.getByText('Value is required')).toBeInTheDocument()
  })

  it('should save edited label value and call onLabelsChange', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // Click on operator label to start editing
    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    // Find the actual input element via displayValue (the current value is 'roakey')
    const editInput = screen.getByDisplayValue('roakey')
    fireEvent.change(editInput, { target: { value: 'alice' } })
    fireEvent.keyDown(editInput, { key: 'Enter' })

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GLOBAL_LABELS,
      operator: 'alice',
    })
  })

  it('should keep the edit you just started when leaving another one', async () => {
    // Both editors finish on blur a turn later. If that late work is not tied
    // to the label it was started for, it ends whichever edit is open by then,
    // and the click that opened it looks like it did nothing.
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: 'alice' } })

    // Leaving the operator schedules its save; the click starts the next edit.
    fireEvent.blur(operatorInput)
    fireEvent.focus(screen.getByTestId('edit-label-operation'))
    fireEvent.click(screen.getByTestId('edit-label-operation'))
    await screen.findByTestId('edit-label-operation')

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    expect(screen.getByTestId('edit-label-operation')).toBeInTheDocument()
    expect(screen.getByTestId('edit-label-operator')).toHaveValue(DEFAULT_GLOBAL_LABELS.operator)
    // The operator edit still went in; only its clean-up was skipped.
    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_GLOBAL_LABELS, operator: 'alice' })
  })

  it('should not clear the value of the edit you just started', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // Leaving the operation picker for the operator, the other way round.
    fireEvent.click(screen.getByTestId('edit-label-operation'))
    const operationInput = await screen.findByTestId('edit-label-operation')
    fireEvent.blur(operationInput)
    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    // An editor that opens empty is the same bug wearing a different hat.
    expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    expect(operatorInput).toHaveValue(DEFAULT_GLOBAL_LABELS.operator)
  })

  it('should keep an edit you come back to while the last one is finishing', async () => {
    // Leaving a label and picking it up again is a different edit, even though
    // it is the same label, so the first one's clean-up must not end it.
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    fireEvent.blur(await screen.findByTestId('edit-label-operator'))
    fireEvent.click(screen.getByTestId('edit-label-operation'))
    await screen.findByTestId('edit-label-operation')
    fireEvent.click(screen.getByTestId('edit-label-operator'))
    await screen.findByTestId('edit-label-operator')

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    expect(screen.getByTestId('edit-label-operator')).toHaveValue(DEFAULT_GLOBAL_LABELS.operator)
  })

  it('should not put one label\'s complaint next to another label', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: '' } })
    fireEvent.blur(operatorInput)
    fireEvent.focus(screen.getByTestId('edit-label-operation'))
    fireEvent.click(screen.getByTestId('edit-label-operation'))
    await screen.findByTestId('edit-label-operation')

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    // The empty operator is simply not saved; the operation is not at fault.
    expect(screen.queryByText('Value is required')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })

  it('should not undo a label chosen while another one was still saving', async () => {
    // The save runs a moment after blur and used to write the labels it saw
    // then, quietly putting back anything picked in between.
    const onChange = jest.fn()
    const { rerender } = render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: 'dana' } })
    fireEvent.blur(operatorInput)

    // Something else changes the labels before the save gets its turn.
    rerender(
      <TestWrapper>
        <LabelsBar
          labels={{ ...DEFAULT_GLOBAL_LABELS, operation: 'op_2026_08_picked' }}
          onLabelsChange={onChange}
        />
      </TestWrapper>
    )

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    expect(onChange).toHaveBeenCalledWith({
      operator: 'dana',
      operation: 'op_2026_08_picked',
    })
  })

  it('should keep a suggestion you picked while the last value was still saving', async () => {
    // Leaving the input schedules a save of what was typed. Picking a
    // suggestion is that same edit finishing another way, so the save it left
    // behind must not put the half-typed value back.
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: { operator: ['alice'] },
    })

    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: 'al' } })

    const suggestion = await screen.findByText('alice')
    fireEvent.blur(operatorInput)
    fireEvent.click(suggestion)

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_GLOBAL_LABELS, operator: 'alice' })
    expect(onChange).not.toHaveBeenCalledWith({ ...DEFAULT_GLOBAL_LABELS, operator: 'al' })
  })

  it('should keep both values when two suggestions are picked in quick succession', async () => {
    // Each edit leaves its own save behind, so remembering only the last one
    // that finished early lets the one before it through with a stale value.
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: { operator: ['alice'], team: ['blue'] },
    })

    const onChange = jest.fn()
    // The real bar is driven by state in App, so a value it commits is on its
    // way back down as a prop while the next edit is already under way.
    const Harness = () => {
      const [labels, setLabels] = useState({ ...DEFAULT_GLOBAL_LABELS, team: 'bravo' })
      return (
        <TestWrapper>
          <LabelsBar
            labels={labels}
            onLabelsChange={next => { onChange(next); setLabels(next) }}
          />
        </TestWrapper>
      )
    }
    render(<Harness />)

    // Wait for the suggestions once, then run the sequence without awaiting
    // anything: both edits have to finish inside the same save delay.
    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: 'al' } })
    const alice = await screen.findByText('alice')
    fireEvent.blur(operatorInput)
    fireEvent.click(alice)

    fireEvent.click(screen.getByTestId('label-team'))
    const teamInput = await screen.findByTestId('edit-label-team')
    fireEvent.change(teamInput, { target: { value: 'bl' } })
    const blue = await screen.findByText('blue')
    fireEvent.blur(teamInput)
    fireEvent.click(blue)

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    const [last] = onChange.mock.calls[onChange.mock.calls.length - 1]
    expect(last).toEqual({ ...DEFAULT_GLOBAL_LABELS, operator: 'alice', team: 'blue' })
  })

  it('should not bring back a label removed while another one was still saving', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar
          labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'blue' }}
          onLabelsChange={onChange}
        />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))
    const operatorInput = await screen.findByTestId('edit-label-operator')
    fireEvent.change(operatorInput, { target: { value: 'dana' } })
    fireEvent.blur(operatorInput)
    fireEvent.click(screen.getByTestId('remove-label-team'))

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    const [last] = onChange.mock.calls[onChange.mock.calls.length - 1]
    expect(last).not.toHaveProperty('team')
    expect(last).toHaveProperty('operator', 'dana')
  })

  it('should not bring back a label removed while it was the one being edited', async () => {
    // The popover editor leaves the label's own chip on the bar, so the label
    // can be taken away while its edit is still finishing.
    const onChange = jest.fn()
    const Harness = () => {
      const [labels, setLabels] = useState({ ...DEFAULT_GLOBAL_LABELS, team: 'green' })
      return (
        <TestWrapper>
          <LabelsBar
            labels={labels}
            onLabelsChange={next => { onChange(next); setLabels(next) }}
          />
        </TestWrapper>
      )
    }
    render(<Harness />)

    fireEvent.click(screen.getByTestId('labels-icon-btn'))
    fireEvent.click(await screen.findByTestId('popover-label-team'))
    const teamInput = await screen.findByTestId('edit-label-team')
    fireEvent.change(teamInput, { target: { value: 'gr' } })
    fireEvent.blur(teamInput)
    fireEvent.click(screen.getByTestId('remove-label-team'))

    await act(async () => { await new Promise(r => setTimeout(r, 400)) })

    const [last] = onChange.mock.calls[onChange.mock.calls.length - 1]
    expect(last).not.toHaveProperty('team')
  })

  it('should cancel edit on Escape key', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    const editInput = screen.getByDisplayValue('roakey')
    fireEvent.keyDown(editInput, { key: 'Escape' })

    // Should not call onChange
    expect(onChange).not.toHaveBeenCalled()
    // Edit mode should be closed - the original label should reappear
    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })
  })

  it('should reject invalid edit value (validation error)', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    const editInput = screen.getByDisplayValue('roakey')
    // Clear the input to empty value
    fireEvent.change(editInput, { target: { value: '' } })
    fireEvent.keyDown(editInput, { key: 'Enter' })

    expect(onChange).not.toHaveBeenCalled()
  })

  it('should add label via Enter keypress in add popover', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    const valueInput = screen.getByPlaceholderText('value')

    fireEvent.change(keyInput, { target: { value: 'env' } })
    fireEvent.change(valueInput, { target: { value: 'prod' } })
    fireEvent.keyDown(valueInput, { key: 'Enter' })

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GLOBAL_LABELS,
      env: 'prod',
    })
  })

  it('should close add popover on Escape key', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    fireEvent.keyDown(keyInput, { key: 'Escape' })

    // Popover should close
    await waitFor(() => {
      expect(screen.queryByTestId('new-label-key')).not.toBeInTheDocument()
    })
  })

  it('should show suggestion chips from fetched labels when adding', async () => {
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: {
        operator: ['alice', 'bob'],
        team: ['red', 'blue'],
        env: ['prod', 'staging'],
      },
    })

    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // Wait for labels to be fetched
    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    // "team" and "env" should appear as suggestions (operator is already used)
    expect(screen.getByText('team')).toBeInTheDocument()
    expect(screen.getByText('env')).toBeInTheDocument()
  })

  it('should show value suggestions when a known key is typed', async () => {
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: {
        operator: ['alice', 'bob'],
        team: ['red', 'blue'],
      },
    })

    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByTestId('labels-icon-btn'))

    await waitFor(() => {
      expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    })

    const keyInput = screen.getByPlaceholderText('key')
    fireEvent.change(keyInput, { target: { value: 'team' } })

    // Value suggestions for "team" should appear
    await waitFor(() => {
      expect(screen.getByText('red')).toBeInTheDocument()
      expect(screen.getByText('blue')).toBeInTheDocument()
    })
  })

  it('should show edit dropdown suggestions when editing a label', async () => {
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: {
        operator: ['alice', 'bob', 'charlie'],
        operation: ['op_one', 'op_two'],
      },
    })

    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    // Click on operator to edit
    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    // Should show suggestions excluding the current value ('roakey')
    // that match the edit text (initially 'roakey' but lowercased)
    const editInput = screen.getByDisplayValue('roakey')
    fireEvent.change(editInput, { target: { value: '' } })

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
      expect(screen.getByText('bob')).toBeInTheDocument()
    })
  })

  it('should select a suggestion from edit dropdown', async () => {
    mockedLabelsApi.getLabels.mockResolvedValueOnce({
      source: 'attacks',
      labels: {
        operator: ['alice', 'bob'],
      },
    })

    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    await waitFor(() => {
      expect(mockedLabelsApi.getLabels).toHaveBeenCalled()
    })

    fireEvent.click(screen.getByTestId('edit-label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    const editInput = screen.getByDisplayValue('roakey')
    fireEvent.change(editInput, { target: { value: '' } })

    await waitFor(() => {
      expect(screen.getByText('alice')).toBeInTheDocument()
    })

    // Click on suggestion
    fireEvent.click(screen.getByText('alice'))

    expect(onChange).toHaveBeenCalledWith({
      ...DEFAULT_GLOBAL_LABELS,
      operator: 'alice',
    })
  })

  it('should not allow removing operator via handleRemoveLabel guard', () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('remove-label-operator')).not.toBeInTheDocument()
    expect(screen.getByTestId('remove-label-operation')).toBeInTheDocument()

    // team should have a remove button
    expect(screen.getByTestId('remove-label-team')).toBeInTheDocument()
  })

  it('always renders a labels icon button with a count of custom labels', () => {
    // The labels icon is always present at the leftmost position with a
    // badge showing the custom-label count, regardless of how many chips
    // happen to fit inline. Clicking it opens a popover with the full
    // label list and the add form.
    render(
      <TestWrapper>
        <LabelsBar
          labels={{ operator: 'alice', operation: 'op_one', team: 'red' }}
          onLabelsChange={jest.fn()}
        />
      </TestWrapper>
    )

    const iconBtn = screen.getByTestId('labels-icon-btn')
    expect(iconBtn).toBeInTheDocument()
    expect(iconBtn).toHaveAttribute('aria-label', 'Labels (1)')
    expect(iconBtn).toHaveTextContent('1')
  })

  it('should show a zero count and no empty-state text when there are no custom labels', async () => {
    render(
      <TestWrapper>
        <LabelsBar
          labels={{ operator: 'alice', operation: 'op_one' }}
          onLabelsChange={jest.fn()}
        />
      </TestWrapper>
    )

    const iconBtn = screen.getByTestId('labels-icon-btn')
    expect(iconBtn).toHaveAttribute('aria-label', 'Labels (0)')
    expect(iconBtn).toHaveTextContent('0')

    fireEvent.click(iconBtn)

    expect(await screen.findByRole('heading', { name: 'Default Labels' })).toBeInTheDocument()
    expect(screen.queryByText('No labels yet')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-label-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-label-operation')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operation')).not.toBeInTheDocument()
  })

  it('icon button opens a popover with custom labels and the add form', async () => {
    const user = userEvent.setup()
    render(
      <TestWrapper>
        <LabelsBar
          labels={{ operator: 'alice', operation: 'op_one', team: 'red' }}
          onLabelsChange={jest.fn()}
        />
      </TestWrapper>
    )

    await user.click(screen.getByRole('button', { name: /^1 label/ }))

    expect(await screen.findByRole('heading', { name: 'Default Labels' })).toBeInTheDocument()
    expect(screen.getByText('added to new attacks and scans')).toBeInTheDocument()
    expect(screen.queryByText('Run metadata')).not.toBeInTheDocument()
    expect(screen.queryByText('Add Label')).not.toBeInTheDocument()
    expect(screen.getByTestId('popover-label-team')).toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operation')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-label-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-label-operation')).not.toBeInTheDocument()
    expect(screen.queryByText('No labels yet')).not.toBeInTheDocument()
    expect(screen.getByTestId('new-label-key')).toBeInTheDocument()
    expect(screen.getByTestId('new-label-value')).toBeInTheDocument()
    expect(screen.getByTestId('confirm-add-label')).toBeInTheDocument()
  })

  it('keeps metadata in the bar and custom labels in the popover when chips do not fit', async () => {
    // Regression guard for the narrow-viewport ribbon bug: even when the
    // available width is too small for every chip to fit, the labels icon
    // (with the custom-label count) and both metadata controls must remain reachable.
    // Tests sub the layout properties to simulate a narrow ribbon.
    const onChange = jest.fn()
    const { container, rerender } = render(
      <TestWrapper>
        <div>
          <LabelsBar
            labels={{ operator: 'alice', operation: 'op_one', team: 'red', env: 'prod' }}
            onLabelsChange={onChange}
          />
        </div>
      </TestWrapper>
    )

    const root = container.querySelector('[data-testid="labels-bar"]') as HTMLElement | null
    if (!root) throw new Error('labels-bar not found')
    Object.defineProperty(root, 'clientWidth', { configurable: true, value: 250 })
    const metadata = screen.getByRole('combobox', { name: 'Operation' }).closest('[data-testid="labels-bar"]')
      ?.querySelector('[data-testid="edit-label-operator"]')?.parentElement?.parentElement?.parentElement?.parentElement
    if (!metadata) throw new Error('metadata controls not found')
    Object.defineProperty(metadata, 'offsetWidth', { configurable: true, value: 240 })
    // Only one 100 px chip fits after reserving room for the icon button.
    const measure = root.querySelector('[aria-hidden="true"]') as HTMLElement | null
    if (measure) {
      const chips = Array.from(measure.querySelectorAll('[data-label-idx]')) as HTMLElement[]
      for (const chip of chips) {
        Object.defineProperty(chip, 'offsetWidth', { configurable: true, value: 100 })
      }
    }

    rerender(
      <TestWrapper>
        <div>
          <LabelsBar
            labels={{ operator: 'alice', operation: 'op_one', team: 'red', env: 'prod', extra: 'x' }}
            onLabelsChange={onChange}
          />
        </div>
      </TestWrapper>
    )

    // The icon button stays visible with the full count (3 custom labels).
    await waitFor(() => {
      const btn = screen.getByTestId('labels-icon-btn')
      expect(btn).toHaveAttribute('aria-label', 'Labels (3)')
    })

    // Metadata remains in the scrollable bar; custom labels stay in the popover.
    expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    expect(screen.getByTestId('edit-label-operation')).toBeInTheDocument()
    expect(screen.queryByTestId('label-team')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('labels-icon-btn'))
    await waitFor(() => {
      expect(screen.getByTestId('popover-label-team')).toBeInTheDocument()
    })
    expect(screen.getByTestId('popover-label-extra')).toBeInTheDocument()

    expect(screen.queryByTestId('popover-metadata-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('popover-metadata-operation')).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId('labels-icon-btn'))
    fireEvent.click(screen.getByTestId('edit-label-operator'))
    expect(await screen.findByTestId('edit-label-operator')).toBeInTheDocument()
    fireEvent.keyDown(screen.getByTestId('edit-label-operator'), { key: 'Enter' })

    fireEvent.click(screen.getByTestId('edit-label-operation'))
    expect(await screen.findByTestId('edit-label-operation')).toBeInTheDocument()
  })

  describe('operation picker', () => {
    const OPERATIONS = ['op_2026_07_grok_45', 'op_2026_08_probe', 'validate-button-test']

    function renderWithOperations(onChange: jest.Mock, operations: string[] = OPERATIONS, operation?: string) {
      jest.mocked(operationsApi.list).mockResolvedValue({
        items: operations.map((name, index) => ({ id: `operation-${index}`, name, created_at: '2026-10-07T16:00:00Z' })),
      })
      mockedLabelsApi.getLabels.mockResolvedValue({
        source: 'attacks',
        operators: ['alice'],
        operations,
        labels: {},
      })
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS, ...(operation ? { operation } : {}) }} onLabelsChange={onChange} />
        </TestWrapper>
      )
    }

    it('should list every operation without clearing the current value first', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      expect(await screen.findByRole('option', { name: 'op_2026_08_probe' })).toBeInTheDocument()
      expect(screen.getByRole('option', { name: 'op_2026_07_grok_45' })).toBeInTheDocument()
      const input = screen.getByTestId('edit-label-operation') as HTMLInputElement
      expect(input.placeholder).toBe('Search operations')
      expect(input.value).toBe('')
    })

    it('should select an existing operation', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.click(await screen.findByRole('option', { name: 'op_2026_08_probe' }))

      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_GLOBAL_LABELS,
        operation: 'op_2026_08_probe',
      })
    })

    it('should select an existing operation that predates the value rules', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.click(await screen.findByRole('option', { name: 'validate-button-test' }))

      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_GLOBAL_LABELS,
        operation: 'validate-button-test',
      })
    })

    it('should filter the options by typed text', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await screen.findByRole('option', { name: 'op_2026_08_probe' })
      fireEvent.change(screen.getByTestId('edit-label-operation'), { target: { value: 'grok' } })

      expect(await screen.findByRole('option', { name: 'op_2026_07_grok_45' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'op_2026_08_probe' })).not.toBeInTheDocument()
    })

    it('should not create a new operation from typed text', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await screen.findByRole('option', { name: 'op_2026_08_probe' })
      fireEvent.change(screen.getByTestId('edit-label-operation'), { target: { value: 'op_2026_09_new' } })
      expect(screen.queryByRole('option', { name: 'Create "op_2026_09_new"' })).not.toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should treat arbitrary punctuation as search, not creation', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await screen.findByRole('option', { name: 'op_2026_08_probe' })
      fireEvent.change(screen.getByTestId('edit-label-operation'), { target: { value: 'bad name!' } })

      expect(await screen.findByRole('option', { name: 'No matching saved operations.' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'Create "bad name!"' })).not.toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should restore saved matches when the search changes', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')
      fireEvent.change(input, { target: { value: 'bad name!' } })
      await screen.findByRole('option', { name: 'No matching saved operations.' })

      fireEvent.change(input, { target: { value: 'probe' } })

      expect(await screen.findByRole('option', { name: 'op_2026_08_probe' })).toBeInTheDocument()
      expect(
        screen.queryByRole('option', { name: 'Only lowercase letters, numbers, underscores' })
      ).not.toBeInTheDocument()
    })

    it('should commit the highlighted option with the keyboard', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')
      // Narrow to a single option so the active option is unambiguous.
      fireEvent.change(input, { target: { value: 'grok' } })
      await screen.findByRole('option', { name: 'op_2026_07_grok_45' })
      fireEvent.keyDown(input, { key: 'ArrowDown' })
      fireEvent.keyDown(input, { key: 'ArrowDown' })
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_GLOBAL_LABELS,
        operation: 'op_2026_07_grok_45',
      })
    })

    it('should dismiss the picker on Escape without committing', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange, OPERATIONS, 'op_2026_08_probe')
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')
      fireEvent.keyDown(input, { key: 'Escape' })

      await waitFor(() => {
        expect(input).toHaveAttribute('aria-expanded', 'false')
        expect(input).toHaveValue('op_2026_08_probe')
      })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should direct users to explicit creation when no saved operations exist', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange, [])
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await waitFor(() => {
        expect(screen.getAllByRole('option')).toHaveLength(1)
        expect(screen.getByRole('option', { name: 'New operation…' })).toBeInTheDocument()
      })

      fireEvent.change(screen.getByTestId('edit-label-operation'), { target: { value: 'op_first' } })
      expect(screen.queryByRole('option', { name: 'Create "op_first"' })).not.toBeInTheDocument()
      expect(screen.getAllByRole('option')[0]).toHaveTextContent('New operation')
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should show a loading option while operations are still being fetched', async () => {
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockImplementation(() => new Promise(() => {}))
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      expect(await screen.findByRole('option', { name: /loading operations/i })).toBeInTheDocument()
    })

    it('should dismiss the picker when the user clicks away', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      await user.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByRole('combobox', { name: 'Operation' })
      await waitFor(() => expect(input).toHaveFocus())
      await user.click(document.body)

      await waitFor(() => {
        expect(input).toHaveAttribute('aria-expanded', 'false')
      })
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should move focus into the picker so it can be driven by keyboard', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      renderWithOperations(onChange, OPERATIONS, 'op_2026_08_probe')
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      // The permanent combobox remains focusable while closed.
      const chip = screen.getByTestId('edit-label-operation')
      expect(chip).toHaveAttribute('role', 'combobox')
      expect(chip).toHaveValue('op_2026_08_probe')
      chip.focus()
      expect(chip).toHaveFocus()
      await user.keyboard('{ArrowDown}')

      const input = await screen.findByTestId('edit-label-operation')
      expect(await screen.findByRole('option', { name: 'op_2026_08_probe' })).toBeInTheDocument()
      await waitFor(() => expect(input).toHaveFocus())
    })

    it('should not commit an operation when the user tabs away', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.focus(screen.getByTestId('edit-label-operation'))
      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await screen.findByTestId('edit-label-operation')
      await user.tab()

      expect(onChange).not.toHaveBeenCalled()
    })

    it('should let focus advance to the next control when tabbing away', async () => {
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockResolvedValue({
        source: 'attacks',
        labels: { operation: OPERATIONS, operator: ['alice'] },
      })
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
          <button data-testid="after">after</button>
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')
      input.focus()
      await waitFor(() => expect(input).toHaveFocus())
      const nextButton = screen.getByRole('button', { name: 'after' })

      // jsdom cannot complete Fluent's Tabster focus-guard navigation, so
      // dispatch Tab and drive its browser focus destination explicitly.
      fireEvent.keyDown(input, { key: 'Tab' })
      nextButton.focus()

      await waitFor(() => expect(input).toHaveAttribute('aria-expanded', 'false'))
      expect(onChange).not.toHaveBeenCalled()
      expect(nextButton).toHaveFocus()
    })

    it('should match existing operations regardless of their casing', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange, ['op_Legacy_Run'])
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')

      // A partial match still finds the differently-cased operation.
      fireEvent.change(input, { target: { value: 'legacy' } })
      expect(await screen.findByRole('option', { name: 'op_Legacy_Run' })).toBeInTheDocument()

      // Typing its full name must not offer to create a case-duplicate.
      fireEvent.change(input, { target: { value: 'op_legacy_run' } })
      expect(await screen.findByRole('option', { name: 'op_Legacy_Run' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'Create "op_legacy_run"' })).not.toBeInTheDocument()
    })

    it('should remove a custom label with the keyboard instead of starting an edit', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockResolvedValue({ source: 'attacks', labels: {} })
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      screen.getByTestId('remove-label-team').focus()
      await user.keyboard('{Enter}')

      expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_GLOBAL_LABELS })
      expect(screen.queryByTestId('edit-label-team')).not.toBeInTheDocument()
    })

    it('should remove a custom label with the keyboard from the popover', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockResolvedValue({ source: 'attacks', labels: {} })
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS, team: 'red' }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('labels-icon-btn'))
      ;(await screen.findByTestId('popover-remove-label-team')).focus()
      await user.keyboard('{Enter}')

      expect(onChange).toHaveBeenCalledWith({ ...DEFAULT_GLOBAL_LABELS })
      expect(screen.queryByTestId('edit-label-team')).not.toBeInTheDocument()
    })

    it('should say so when the operations could not be loaded', async () => {
      const onChange = jest.fn()
      jest.mocked(operationsApi.list).mockRejectedValueOnce(new Error('boom'))
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      expect(
        await screen.findByRole('option', { name: /could not load saved operations/i })
      ).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: /no operations yet/i })).not.toBeInTheDocument()
    })

    it('should not select an unsaved typed name with the keyboard', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')
      fireEvent.change(input, { target: { value: 'op_2026_09_typed' } })
      await screen.findByRole('option', { name: 'No matching saved operations.' })
      fireEvent.keyDown(input, { key: 'Enter' })

      expect(onChange).not.toHaveBeenCalled()
    })

    it('should refresh saved operations when reopening the picker', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.keyDown(await screen.findByTestId('edit-label-operation'), { key: 'Escape' })
      jest.mocked(operationsApi.list).mockResolvedValue({
        items: [{ id: 'fresh', name: 'op_2026_09_fresh', created_at: '2026-10-07T16:00:00Z' }],
      })

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      expect(await screen.findByRole('option', { name: 'op_2026_09_fresh' })).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'Create "op_2026_09_fresh"' })).not.toBeInTheDocument()
    })

    it('should allow removing a legacy selected operation', async () => {
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockResolvedValue({
        source: 'attacks',
        labels: { operation: OPERATIONS, operator: ['alice'] },
      })
      render(
        <TestWrapper>
          <LabelsBar
            labels={{ ...DEFAULT_GLOBAL_LABELS, operation: 'legacy-op-name.2024' }}
            onLabelsChange={onChange}
          />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('remove-label-operation'))
      expect(onChange).toHaveBeenCalledWith({ operator: FRESH_GLOBAL_LABELS.operator })
      expect(screen.queryByText(/Only lowercase letters/)).not.toBeInTheDocument()
    })

    it('should not accept a typed name while the saved list is loading or failed', async () => {
      const onChange = jest.fn()
      let rejectLabels: (reason: Error) => void = () => {}
      jest.mocked(operationsApi.list).mockReturnValueOnce(
        new Promise((_resolve, reject) => { rejectLabels = reject })
      )
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.change(await screen.findByTestId('edit-label-operation'), {
        target: { value: 'op_made_during_load' },
      })
      expect(onChange).not.toHaveBeenCalled()

      await act(async () => {
        rejectLabels(new Error('boom'))
      })

      expect(
        await screen.findByRole('option', { name: /Could not load saved operations/ })
      ).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'op_made_during_load' })).not.toBeInTheDocument()
    })

    it('should still say the operations could not be loaded when one is already set', async () => {
      const onChange = jest.fn()
      jest.mocked(operationsApi.list).mockRejectedValue(new Error('boom'))
      render(
        <TestWrapper>
          <LabelsBar
            labels={{ ...DEFAULT_GLOBAL_LABELS, operation: 'op_already_set' }}
            onLabelsChange={onChange}
          />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      expect(
        await screen.findByRole('option', { name: /Could not load saved operations/ })
      ).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'op_already_set' })).not.toBeInTheDocument()
    })

    it('should keep the operation in use on a capped list that already contains it', async () => {
      // The saved list usually does contain the operation in use, and it can
      // sit anywhere in it — including past the cap.
      const onChange = jest.fn()
      const many = Array.from({ length: 250 }, (_, i) => `op_2026_08_run_${String(i).padStart(4, '0')}`)
      mockedLabelsApi.getLabels.mockResolvedValue({
        source: 'attacks',
        labels: { operation: many, operator: ['alice'] },
      })
      jest.mocked(operationsApi.list).mockResolvedValue({
        items: many.map((name, index) => ({ id: `op-${index}`, name, created_at: '2026-10-07T16:00:00Z' })),
      })
      render(
        <TestWrapper>
          <LabelsBar
            labels={{ ...DEFAULT_GLOBAL_LABELS, operation: 'op_2026_08_run_0240' }}
            onLabelsChange={onChange}
          />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))

      // Listed once, not twice, even though it is also in the saved list.
      expect(await screen.findAllByRole('option', { name: 'op_2026_08_run_0240' })).toHaveLength(1)
      expect(screen.getByText('Showing 200 of 250 — type to narrow')).toBeInTheDocument()
    })

    it('should keep a name typed in full on a capped list', async () => {
      // Every decoy contains the typed name, so the exact match sorts last and
      // the cap would hide it — leaving Enter to commit a different operation.
      const onChange = jest.fn()
      const decoys = Array.from({ length: 250 }, (_, i) => `op_2026_08_run_042_${String(i).padStart(3, '0')}`)
      renderWithOperations(onChange, [...decoys, 'run_042'].sort())
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.change(await screen.findByTestId('edit-label-operation'), {
        target: { value: 'run_042' },
      })

      const exact = await screen.findByRole('option', { name: 'run_042' })
      expect(exact).toBeInTheDocument()
      // It is not offered for creation, because it already exists.
      expect(screen.queryByRole('option', { name: 'Create "run_042"' })).not.toBeInTheDocument()

      fireEvent.click(exact)
      expect(onChange).toHaveBeenCalledWith({
        ...DEFAULT_GLOBAL_LABELS,
        operation: 'run_042',
      })
    })

    it('should show only the first page of a long list and say so', async () => {
      const onChange = jest.fn()
      const many = Array.from({ length: 250 }, (_, i) => `op_2026_08_run_${String(i).padStart(4, '0')}`)
      renderWithOperations(onChange, many)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      await screen.findByRole('option', { name: 'op_2026_08_run_0000' })

      expect(screen.getAllByRole('option')).toHaveLength(202)
      expect(screen.getByText('Showing 200 of 250 — type to narrow')).toBeInTheDocument()
      expect(screen.queryByRole('option', { name: 'op_2026_08_run_0249' })).not.toBeInTheDocument()

      // Typing narrows it below the cap, and then the note goes away.
      fireEvent.change(screen.getByTestId('edit-label-operation'), {
        target: { value: 'run_024' },
      })
      expect(await screen.findByRole('option', { name: 'op_2026_08_run_0249' })).toBeInTheDocument()
      expect(screen.queryByText(/type to narrow/)).not.toBeInTheDocument()
    })

    it('should not offer the cap note as something to choose', async () => {
      const onChange = jest.fn()
      const many = Array.from({ length: 250 }, (_, i) => `op_2026_08_run_${String(i).padStart(4, '0')}`)
      renderWithOperations(onChange, many)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const note = await screen.findByRole('option', { name: /type to narrow/ })

      expect(note).toHaveAttribute('aria-disabled', 'true')
      fireEvent.click(note)
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should keep saying the operations could not be loaded while a name is typed', async () => {
      // The note answers "why is this list empty"; typing does not answer it.
      const onChange = jest.fn()
      jest.mocked(operationsApi.list).mockRejectedValueOnce(new Error('boom'))
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const input = await screen.findByTestId('edit-label-operation')

      fireEvent.change(input, { target: { value: 'op_2026_09_typed' } })
      expect(screen.queryByRole('option', { name: 'Create "op_2026_09_typed"' })).not.toBeInTheDocument()
      expect(
        await screen.findByRole('option', { name: /Could not load saved operations/ })
      ).toBeInTheDocument()

      fireEvent.change(input, { target: { value: 'op bad' } })
      expect(screen.queryByText(/Only lowercase letters/)).not.toBeInTheDocument()
      expect(
        screen.getByRole('option', { name: /Could not load saved operations/ })
      ).toBeInTheDocument()
    })

    it('should not offer a status note as something to choose', async () => {
      // The notes share the option list with real values, so they have to be
      // unselectable or one of them becomes the operation.
      const onChange = jest.fn()
      jest.mocked(operationsApi.list).mockRejectedValueOnce(new Error('boom'))
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      const failed = await screen.findByRole('option', {
        name: /Could not load saved operations/,
      })
      expect(failed).toHaveAttribute('aria-disabled', 'true')

      fireEvent.change(await screen.findByTestId('edit-label-operation'), {
        target: { value: 'op bad' },
      })
      fireEvent.click(failed)
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should offer only New operation for an empty saved list, including while typing', async () => {
      const user = userEvent.setup()
      const onChange = jest.fn()
      mockedLabelsApi.getLabels.mockResolvedValue({
        source: 'attacks',
        labels: { operation: [], operator: ['alice'] },
      })
      jest.mocked(operationsApi.list).mockResolvedValue({ items: [] })
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      const input = screen.getByRole('combobox', { name: 'Operation' })
      await user.click(input)
      await waitFor(() => {
        expect(screen.getAllByRole('option')).toHaveLength(1)
        expect(screen.getByRole('option', { name: 'New operation…' })).toBeInTheDocument()
      })

      await user.type(input, 'op_2026_09_first')
      expect(screen.queryByRole('option', { name: 'Create "op_2026_09_first"' })).not.toBeInTheDocument()
      expect(screen.getAllByRole('option')).toHaveLength(1)

      await user.clear(input)
      await user.type(input, 'op bad')
      expect(screen.queryByText(/Only lowercase letters/)).not.toBeInTheDocument()
      expect(screen.getAllByRole('option')).toHaveLength(1)
      expect(screen.getByRole('option', { name: 'New operation…' })).toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should not select a typed name when a delayed saved list arrives', async () => {
      const onChange = jest.fn()
      let resolveLabels: (value: { items: Array<{ id: string; name: string; created_at: string }> }) => void = () => {}
      jest.mocked(operationsApi.list).mockReturnValueOnce(
        new Promise(resolve => { resolveLabels = resolve })
      )
      render(
        <TestWrapper>
          <LabelsBar labels={{ ...FRESH_GLOBAL_LABELS }} onLabelsChange={onChange} />
        </TestWrapper>
      )

      fireEvent.click(screen.getByTestId('edit-label-operation'))
      fireEvent.change(await screen.findByTestId('edit-label-operation'), {
        target: { value: 'op_made_while_loading' },
      })

      await act(async () => {
        resolveLabels({ items: [{ id: 'server', name: 'op_from_server', created_at: '2026-10-07T16:00:00Z' }] })
      })
      expect(screen.queryByRole('option', { name: 'op_made_while_loading' })).not.toBeInTheDocument()
      fireEvent.change(screen.getByTestId('edit-label-operation'), { target: { value: '' } })
      expect(screen.getByRole('option', { name: 'op_from_server' })).toBeInTheDocument()
      expect(onChange).not.toHaveBeenCalled()
    })

    it('should keep the plain input for labels other than operation', async () => {
      const onChange = jest.fn()
      renderWithOperations(onChange)
      await waitFor(() => expect(mockedLabelsApi.getLabels).toHaveBeenCalled())

      fireEvent.click(screen.getByTestId('edit-label-operator'))

      expect(await screen.findByTestId('edit-label-operator')).toBeInTheDocument()
      expect(screen.queryByRole('option')).not.toBeInTheDocument()
    })
  })

})
