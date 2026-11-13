import { useId, useState, type ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { IdentityDisclosureButton, IdentityJson } from './IdentityDisclosure'

const IDENTITY = { class_name: 'Crescendo', parameters: { max_turns: 5 } }

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function Disclosure() {
  const id = useId()
  const [expanded, setExpanded] = useState(false)
  return <>
    <IdentityDisclosureButton name="Crescendo" expanded={expanded} controlsId={id}
      onToggle={() => { setExpanded((current: boolean) => !current) }} />
    {expanded && <IdentityJson id={id} name="Crescendo" identity={IDENTITY} />}
  </>
}

describe('IdentityDisclosure', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should reveal and hide the identity JSON from the name', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><Disclosure /></TestWrapper>)

    const toggle = screen.getByRole('button', { name: 'Identity for Crescendo' })
    expect(toggle).toHaveTextContent('Crescendo')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()

    await user.click(toggle)
    const details = screen.getByRole('region', { name: 'Identity for Crescendo' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveAttribute('aria-controls', details.id)
    expect(JSON.parse(details.textContent ?? '')).toEqual(IDENTITY)
    expect(details.scrollIntoView).toHaveBeenCalled()

    await user.click(toggle)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })

  it('should not trigger an enclosing row when toggled', async () => {
    const user = userEvent.setup()
    const onRowClick = jest.fn()
    const onToggle = jest.fn()
    render(<TestWrapper>
      <div onClick={onRowClick}>
        <IdentityDisclosureButton name="Crescendo" expanded={false} controlsId="identity" onToggle={onToggle} />
      </div>
    </TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Identity for Crescendo' }))
    expect(onToggle).toHaveBeenCalledTimes(1)
    expect(onRowClick).not.toHaveBeenCalled()
  })
})
