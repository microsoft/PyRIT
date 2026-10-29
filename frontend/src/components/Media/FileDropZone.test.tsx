import { fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import FileDropZone from './FileDropZone'

const TestWrapper = ({ children }: { children: React.ReactNode }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)
const file = new File(['image'], 'sample.png', { type: 'image/png' })
const dataTransfer = { types: ['Files'], files: [file] }

describe('FileDropZone', () => {
  beforeEach(() => jest.clearAllMocks())

  it('should deliver a nested drop only once and clear feedback from both surfaces', () => {
    const outer = jest.fn()
    const inner = jest.fn()
    render(<TestWrapper>
      <FileDropZone onFiles={outer} testId="outer">
        <FileDropZone onFiles={inner} testId="inner">Content</FileDropZone>
      </FileDropZone>
    </TestWrapper>)
    fireEvent.dragEnter(screen.getByTestId('outer'), { dataTransfer })
    fireEvent.dragEnter(screen.getByTestId('inner'), { dataTransfer })
    expect(screen.getAllByRole('status')).toHaveLength(2)
    fireEvent.drop(screen.getByTestId('inner'), { dataTransfer })
    expect(inner).toHaveBeenCalledWith([file])
    expect(inner).toHaveBeenCalledTimes(1)
    expect(outer).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('should retain feedback across children and clear it when a file drag leaves without types', () => {
    render(<TestWrapper><FileDropZone onFiles={jest.fn()} testId="zone"><span>Child</span></FileDropZone></TestWrapper>)
    const zone = screen.getByTestId('zone')
    fireEvent.dragEnter(zone, { dataTransfer })
    fireEvent.dragEnter(screen.getByText('Child'), { dataTransfer })
    fireEvent.dragLeave(screen.getByText('Child'), { dataTransfer: { types: [] } })
    expect(screen.getByRole('status')).toBeVisible()
    fireEvent.dragLeave(zone, { dataTransfer: { types: [] } })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each(['escape', 'blur', 'dragend', 'outside-drop'])('should clear feedback on %s', async (action: string) => {
    const user = userEvent.setup()
    const onFiles = jest.fn()
    render(<TestWrapper><FileDropZone onFiles={onFiles} testId="zone">Content</FileDropZone></TestWrapper>)
    fireEvent.dragEnter(screen.getByTestId('zone'), { dataTransfer })
    if (action === 'escape') await user.keyboard('{Escape}')
    else if (action === 'blur') fireEvent.blur(window)
    else if (action === 'dragend') fireEvent.dragEnd(document)
    else fireEvent.drop(document, { dataTransfer })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(onFiles).not.toHaveBeenCalled()
  })

  it('should ignore text drops without parsing them or blocking native text behavior', () => {
    const onFiles = jest.fn()
    render(<TestWrapper><FileDropZone onFiles={onFiles} testId="zone">Content</FileDropZone></TestWrapper>)
    const transfer = { types: ['text/plain'], files: [], getData: jest.fn() }
    expect(fireEvent.drop(screen.getByTestId('zone'), { dataTransfer: transfer })).toBe(true)
    expect(onFiles).not.toHaveBeenCalled()
    expect(transfer.getData).not.toHaveBeenCalled()
  })

  it('should block disabled drops and end feedback without remounting an existing draft', async () => {
    const user = userEvent.setup()
    const onFiles = jest.fn()
    const tree = (disabled: boolean) => <TestWrapper>
      <FileDropZone onFiles={onFiles} disabled={disabled} testId="zone"><input aria-label="Draft" /></FileDropZone>
    </TestWrapper>
    const { rerender } = render(tree(false))
    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'Keep my draft')
    fireEvent.dragEnter(screen.getByTestId('zone'), { dataTransfer })
    rerender(tree(true))
    expect(fireEvent.drop(screen.getByTestId('zone'), { dataTransfer })).toBe(false)
    rerender(tree(false))
    expect(onFiles).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('textbox', { name: 'Draft' })).toHaveValue('Keep my draft')
  })
})
