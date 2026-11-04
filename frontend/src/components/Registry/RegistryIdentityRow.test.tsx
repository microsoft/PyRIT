import { useState } from 'react'

import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, Table, TableBody, TableCell, webLightTheme } from '@fluentui/react-components'

import RegistryIdentityRow from './RegistryIdentityRow'

const IDENTITY = {
  class_name: 'Example',
  hash: 'identity-hash',
  children: { converters: [{ class_name: 'Base64Converter', hash: 'child-hash' }] },
}

function RegistryRows() {
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  return (
    <FluentProvider theme={webLightTheme}>
      <Table><TableBody>
        {['first', 'second'].map((name: string) => (
          <RegistryIdentityRow key={name} name={name} identity={IDENTITY} columns={2}
            expanded={expanded.has(name)} onToggle={() => {
              setExpanded((current: Set<string>) => {
                const next = new Set(current)
                if (next.has(name)) next.delete(name)
                else next.add(name)
                return next
              })
            }}>
            <TableCell>Type</TableCell>
          </RegistryIdentityRow>
        ))}
      </TableBody></Table>
    </FluentProvider>
  )
}

describe('RegistryIdentityRow', () => {
  it('should expand JSON only with Enter and collapse with Space without losing focus', async () => {
    const user = userEvent.setup()
    render(<RegistryRows />)
    const toggle = screen.getByRole('button', { name: 'Identity for first' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    toggle.focus()
    await user.keyboard('{Enter}')
    const details = screen.getByRole('region', { name: 'Identity for first' })
    expect(toggle).toHaveAttribute('aria-controls', details.id)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(details.textContent).toBe(JSON.stringify(IDENTITY, null, 2))
    expect(within(details).queryByRole('button')).not.toBeInTheDocument()
    expect(within(details).queryByRole('heading')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
    await user.keyboard(' ')
    expect(screen.queryByRole('region')).not.toBeInTheDocument()
    expect(toggle).toHaveFocus()
  })

  it('should keep each row independent and use distinct detail IDs', async () => {
    const user = userEvent.setup()
    render(<RegistryRows />)
    await user.click(screen.getByRole('button', { name: 'Identity for first' }))
    await user.click(screen.getByRole('button', { name: 'Identity for second' }))
    const first = screen.getByRole('region', { name: 'Identity for first' })
    const second = screen.getByRole('region', { name: 'Identity for second' })
    expect(first.id).not.toBe(second.id)
    await user.click(screen.getByRole('button', { name: 'Identity for first' }))
    expect(screen.queryByRole('region', { name: 'Identity for first' })).not.toBeInTheDocument()
    expect(second).toBeVisible()
  })
})
