import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import LabelsBar from './LabelsBar'
import { DEFAULT_GLOBAL_LABELS } from './labelDefaults'
import { labelsApi } from '../../services/api'

jest.mock('../../services/api', () => ({
  labelsApi: {
    getLabels: jest.fn(),
  },
}))

const mockedLabelsApi = labelsApi as jest.Mocked<typeof labelsApi>

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('LabelsBar', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockedLabelsApi.getLabels.mockImplementation(() => new Promise(() => {}))
  })

  it('should render default labels', () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    expect(screen.getByTestId('label-operator')).toBeInTheDocument()
    expect(screen.getByTestId('label-operation')).toBeInTheDocument()
    expect(screen.getByText('roakey')).toBeInTheDocument()
    expect(screen.getByText('op_trash_panda')).toBeInTheDocument()
  })

  it('should show warning icon for dummy values', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    expect(screen.getByTestId('labels-warning')).toBeInTheDocument()
  })

  it('should not show warning when values are customized', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ operator: 'alice', operation: 'my_test' }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    expect(screen.queryByTestId('labels-warning')).not.toBeInTheDocument()
  })

  it('should not allow removing required labels (operator, operation)', () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    // operator and operation should not have remove buttons
    expect(screen.queryByTestId('remove-label-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('remove-label-operation')).not.toBeInTheDocument()
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

  it('should add a new label via popover', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

  it('should allow editing a label value by clicking on it', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    // Click on operator label to edit
    fireEvent.click(screen.getByTestId('label-operator'))

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
    expect(screen.getByTestId('label-operator')).toBeInTheDocument()
  })

  it('should reject empty key when adding a label', async () => {
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={jest.fn()} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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
    fireEvent.click(screen.getByTestId('label-operator'))

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

  it('should cancel edit on Escape key', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('label-operator'))

    await waitFor(() => {
      expect(screen.getByTestId('edit-label-operator')).toBeInTheDocument()
    })

    const editInput = screen.getByDisplayValue('roakey')
    fireEvent.keyDown(editInput, { key: 'Escape' })

    // Should not call onChange
    expect(onChange).not.toHaveBeenCalled()
    // Edit mode should be closed - the original label should reappear
    await waitFor(() => {
      expect(screen.getByTestId('label-operator')).toBeInTheDocument()
    })
  })

  it('should reject invalid edit value (validation error)', async () => {
    const onChange = jest.fn()
    render(
      <TestWrapper>
        <LabelsBar labels={{ ...DEFAULT_GLOBAL_LABELS }} onLabelsChange={onChange} />
      </TestWrapper>
    )

    fireEvent.click(screen.getByTestId('label-operator'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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

    fireEvent.click(screen.getByTestId('add-label-btn'))

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
    fireEvent.click(screen.getByTestId('label-operator'))

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

    fireEvent.click(screen.getByTestId('label-operator'))

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

    // operator and operation should not have remove buttons (already tested)
    expect(screen.queryByTestId('remove-label-operator')).not.toBeInTheDocument()
    expect(screen.queryByTestId('remove-label-operation')).not.toBeInTheDocument()

    // team should have a remove button
    expect(screen.getByTestId('remove-label-team')).toBeInTheDocument()
  })
})
