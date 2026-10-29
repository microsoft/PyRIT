import { useState } from 'react'

import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Dialog, DialogBody, DialogSurface, DialogTitle, FluentProvider, webLightTheme } from '@fluentui/react-components'

import { usePreventFileNavigation } from '@/hooks/usePreventFileNavigation'

import FileDropZone from './FileDropZone'

const TestWrapper = ({ children }: { children: React.ReactNode }) => {
  usePreventFileNavigation()
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}
const file = new File(['image'], 'sample.png', { type: 'image/png' })
const dataTransfer = { types: ['Files'], files: [file] }

function leaveFileDrag(target: Node | Window, relatedTarget: EventTarget | null, clientX: number = 100, types: string[] = ['Files']): void {
  const event = new MouseEvent('dragleave', { bubbles: true, cancelable: true, relatedTarget, clientX, clientY: 100 })
  Object.defineProperty(event, 'dataTransfer', { value: { types } })
  fireEvent(target, event)
}

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
    leaveFileDrag(screen.getByText('Child'), null, 100, [])
    expect(screen.getByRole('status')).toBeVisible()
    leaveFileDrag(zone, null, 100, [])
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('should ignore a portal dialog and prevent its unhandled file drop from navigating', () => {
    const onFiles = jest.fn()
    render(<TestWrapper><FileDropZone onFiles={onFiles}>
      <Dialog open><DialogSurface><DialogBody>
        <DialogTitle>Attachment details</DialogTitle><input aria-label="Dialog notes" />
      </DialogBody></DialogSurface></Dialog>
    </FileDropZone></TestWrapper>)
    const input = screen.getByRole('textbox', { name: 'Dialog notes' })
    fireEvent.dragEnter(input, { dataTransfer })
    expect(fireEvent.dragOver(input, { dataTransfer })).toBe(false)
    expect(fireEvent.drop(input, { dataTransfer })).toBe(false)
    expect(onFiles).not.toHaveBeenCalled()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it.each(['outside-drop', 'leave-window'])('should clear OS-file drag feedback on %s', (action: string) => {
    const onFiles = jest.fn()
    render(<TestWrapper><FileDropZone onFiles={onFiles} testId="zone">Content</FileDropZone></TestWrapper>)
    fireEvent.dragEnter(screen.getByTestId('zone'), { dataTransfer })
    if (action === 'leave-window') leaveFileDrag(window, null, -1)
    else expect(fireEvent.drop(document, { dataTransfer })).toBe(false)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(onFiles).not.toHaveBeenCalled()
  })

  it('should clear feedback when an auto-update removes the node under the file drag', async () => {
    const user = userEvent.setup()
    function UpdatingSurface() {
      const [visible, setVisible] = useState(true)
      return <><button onClick={() => setVisible(false)}>Update content</button>
        <FileDropZone onFiles={jest.fn()}>{visible && <span>Old message</span>}</FileDropZone></>
    }
    render(<TestWrapper><UpdatingSurface /></TestWrapper>)
    fireEvent.dragEnter(screen.getByText('Old message'), { dataTransfer })
    expect(screen.getByRole('status')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Update content' }))
    await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument())
  })

  it('should follow related targets without requiring balanced drag-enter and drag-leave events', () => {
    render(<TestWrapper><FileDropZone onFiles={jest.fn()} testId="zone">
      <span>First</span><span>Second</span>
    </FileDropZone><span>Outside</span></TestWrapper>)
    const first = screen.getByText('First')
    const second = screen.getByText('Second')
    fireEvent.dragEnter(first, { dataTransfer })
    leaveFileDrag(first, second)
    expect(screen.getByRole('status')).toBeVisible()
    leaveFileDrag(second, screen.getByText('Outside'))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('should allow the outer chat surface to own a composer drop with only one feedback zone', () => {
    const outer = jest.fn()
    const inner = jest.fn()
    render(<TestWrapper><FileDropZone onFiles={outer} testId="chat">
      <FileDropZone onFiles={inner} enabled={false} testId="composer"><textarea aria-label="Prompt" /></FileDropZone>
    </FileDropZone></TestWrapper>)
    fireEvent.dragEnter(screen.getByRole('textbox', { name: 'Prompt' }), { dataTransfer })
    expect(screen.getAllByRole('status')).toHaveLength(1)
    fireEvent.drop(screen.getByRole('textbox', { name: 'Prompt' }), { dataTransfer })
    expect(outer).toHaveBeenCalledWith([file])
    expect(inner).not.toHaveBeenCalled()
  })

  it('should guard window-level file drops, leave handled drops alone and remove its listeners on unmount', () => {
    const { unmount } = render(<TestWrapper>Content</TestWrapper>)
    const transfer = { types: ['Files'], dropEffect: 'copy' }
    expect(fireEvent.dragOver(window, { dataTransfer: transfer })).toBe(false)
    expect(transfer.dropEffect).toBe('none')
    expect(fireEvent.drop(window, { dataTransfer: transfer })).toBe(false)
    transfer.dropEffect = 'copy'
    const handled = new Event('dragover', { cancelable: true })
    Object.defineProperty(handled, 'dataTransfer', { value: transfer })
    handled.preventDefault()
    window.dispatchEvent(handled)
    expect(transfer.dropEffect).toBe('copy')
    expect(fireEvent.drop(window, { dataTransfer: { types: ['text/plain'] } })).toBe(true)
    unmount()
    expect(fireEvent.drop(window, { dataTransfer: transfer })).toBe(true)
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
