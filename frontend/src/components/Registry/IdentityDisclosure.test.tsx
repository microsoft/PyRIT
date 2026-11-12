import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import IdentityDisclosure from './IdentityDisclosure'

const IDENTITY = { class_name: 'Crescendo', parameters: { max_turns: 5 } }

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('IdentityDisclosure', () => {
  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should reveal and hide the identity JSON from the name', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><IdentityDisclosure name="Crescendo" identity={IDENTITY} /></TestWrapper>)

    const toggle = screen.getByRole('button', { name: 'Identity for Crescendo' })
    expect(toggle).toHaveTextContent('Crescendo')
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()

    await user.click(toggle)
    const details = screen.getByRole('region', { name: 'Identity for Crescendo' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(toggle).toHaveAttribute('aria-controls', details.id)
    expect(JSON.parse(details.textContent ?? '')).toEqual(IDENTITY)

    await user.click(toggle)
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })

  it.each([null, undefined])('should show only the name without an identity (%p)', (identity: null | undefined) => {
    render(<TestWrapper><IdentityDisclosure name="Not recorded" identity={identity} /></TestWrapper>)
    expect(screen.getByText('Not recorded')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('should not trigger an enclosing row when toggled', async () => {
    const user = userEvent.setup()
    const onRowClick = jest.fn()
    render(<TestWrapper>
      <div onClick={onRowClick}><IdentityDisclosure name="Crescendo" identity={IDENTITY} /></div>
    </TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Identity for Crescendo' }))
    expect(onRowClick).not.toHaveBeenCalled()
  })
})
