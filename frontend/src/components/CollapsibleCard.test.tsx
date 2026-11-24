import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Button, FluentProvider, webLightTheme } from '@fluentui/react-components'

import CollapsibleCard from './CollapsibleCard'

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('CollapsibleCard', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('names its toggle with the title and meta in a level-3 heading, with description and actions outside', () => {
    render(
      <TestWrapper>
        <CollapsibleCard
          title="release_gate"
          meta="3 scenarios"
          description="Runs before every release."
          actions={<Button>Launch release_gate</Button>}
        >
          <p>Panel content</p>
        </CollapsibleCard>
      </TestWrapper>,
    )

    const heading = screen.getByRole('heading', { level: 3 })
    const toggle = screen.getByRole('button', { name: 'release_gate 3 scenarios' })
    expect(heading).toContainElement(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(heading).not.toHaveTextContent('Runs before every release.')
    expect(toggle).not.toContainElement(screen.getByRole('button', { name: 'Launch release_gate' }))
    expect(screen.getByText('Runs before every release.')).toBeInTheDocument()
    expect(screen.queryByText('Panel content')).not.toBeInTheDocument()
  })

  it('opens and closes its panel from the heading on its own', async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    render(
      <TestWrapper>
        <CollapsibleCard title="release_gate" onOpenChange={onOpenChange}>
          <p>Panel content</p>
        </CollapsibleCard>
      </TestWrapper>,
    )

    const toggle = screen.getByRole('button', { name: 'release_gate' })
    await user.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Panel content')).toBeInTheDocument()
    await user.keyboard('{Enter}')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(onOpenChange.mock.calls).toEqual([[true], [false]])
  })

  it('follows the open state its parent controls', async () => {
    const user = userEvent.setup()
    const onOpenChange = jest.fn()
    const { rerender } = render(
      <TestWrapper>
        <CollapsibleCard title="release_gate" open={false} onOpenChange={onOpenChange}>
          <p>Panel content</p>
        </CollapsibleCard>
      </TestWrapper>,
    )

    await user.click(screen.getByRole('button', { name: 'release_gate' }))
    expect(onOpenChange).toHaveBeenCalledWith(true)
    expect(screen.queryByText('Panel content')).not.toBeInTheDocument()

    rerender(
      <TestWrapper>
        <CollapsibleCard title="release_gate" open onOpenChange={onOpenChange}>
          <p>Panel content</p>
        </CollapsibleCard>
      </TestWrapper>,
    )
    expect(screen.getByText('Panel content')).toBeInTheDocument()
  })
})
