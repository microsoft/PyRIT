import { useState } from 'react'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, FluentProvider, Tab, TabList, webLightTheme } from '@fluentui/react-components'

import ConfirmDialog from './ConfirmDialog'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

function DialogWithTabs() {
  const [open, setOpen] = useState(false)

  return (
    <>
      <TabList defaultSelectedValue="configuration">
        <Tab value="configuration">Configuration</Tab>
        <Tab value="environment">Environment</Tab>
      </TabList>
      <textarea aria-label="Draft" />
      <Button onClick={() => setOpen(true)}>Open dialog</Button>
      <ConfirmDialog
        open={open}
        title="Discard draft?"
        onConfirm={() => setOpen(false)}
        onCancel={() => setOpen(false)}
      >
        The draft will be lost.
        <span hidden><button type="button">Hidden action</button></span>
        <button type="button" disabled>Unavailable action</button>
      </ConfirmDialog>
    </>
  )
}

describe('ConfirmDialog', () => {
  const defaultProps = {
    open: true,
    title: 'Delete item',
    onConfirm: jest.fn(),
    onCancel: jest.fn(),
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render title and content when open', () => {
    render(
      <TestWrapper>
        <ConfirmDialog {...defaultProps}>Are you sure?</ConfirmDialog>
      </TestWrapper>,
    )

    expect(screen.getByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('Delete item')).toBeInTheDocument()
    expect(screen.getByText('Are you sure?')).toBeInTheDocument()
  })

  it('should not render when closed', () => {
    render(
      <TestWrapper>
        <ConfirmDialog {...defaultProps} open={false}>Are you sure?</ConfirmDialog>
      </TestWrapper>,
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('should call onConfirm when confirm button is clicked', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <ConfirmDialog {...defaultProps}>Are you sure?</ConfirmDialog>
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(defaultProps.onConfirm).toHaveBeenCalledTimes(1)
    expect(defaultProps.onCancel).not.toHaveBeenCalled()
  })

  it('should call onCancel when cancel button is clicked', async () => {
    const user = userEvent.setup()

    render(
      <TestWrapper>
        <ConfirmDialog {...defaultProps}>Are you sure?</ConfirmDialog>
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'Cancel' }))

    expect(defaultProps.onCancel).toHaveBeenCalledTimes(1)
    expect(defaultProps.onConfirm).not.toHaveBeenCalled()
  })

  it('should use custom button labels', () => {
    render(
      <TestWrapper>
        <ConfirmDialog {...defaultProps} confirmLabel="Remove" cancelLabel="Keep">
          Content
        </ConfirmDialog>
      </TestWrapper>,
    )

    expect(screen.getByRole('button', { name: 'Remove' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Keep' })).toBeInTheDocument()
  })

  it('should focus the cancel action each time a dialog opens alongside tabs', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><DialogWithTabs /></TestWrapper>)

    await user.type(screen.getByRole('textbox', { name: 'Draft' }), 'Unsaved draft')

    for (let attempt = 0; attempt < 3; attempt++) {
      await user.click(screen.getByRole('button', { name: 'Open dialog' }))
      await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toHaveFocus())

      await user.keyboard('{Escape}')
      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
      expect(await screen.findByRole('textbox', { name: 'Draft' })).toHaveValue('Unsaved draft')
    }
  })
})
