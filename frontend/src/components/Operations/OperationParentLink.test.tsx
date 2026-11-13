import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'

import OperationParentLink from './OperationParentLink'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}><MemoryRouter>{children}</MemoryRouter></FluentProvider>
}

describe('OperationParentLink', () => {
  it('should link to the operation by name', () => {
    render(<TestWrapper><OperationParentLink id="operation/1" name="Spring review" /></TestWrapper>)
    const link = screen.getByRole('link', { name: 'Operation: Spring review' })
    expect(link).toHaveAttribute('href', '/operations/operation%2F1')
    expect(link).toHaveTextContent('Spring review')
  })

  it('should still link when the operation name is unknown', () => {
    render(<TestWrapper><OperationParentLink id="operation-1" name={null} /></TestWrapper>)
    expect(screen.getByRole('link', { name: 'Operation: View operation' })).toHaveAttribute('href', '/operations/operation-1')
  })

  it.each([undefined, null, ''])('should render nothing without an operation id (%p)', (id: string | null | undefined) => {
    render(<TestWrapper><OperationParentLink id={id} name="Spring review" /></TestWrapper>)
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
  })
})
