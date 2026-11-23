import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'

import AttackActivity from './AttackActivity'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('AttackActivity', () => {
  it('should describe an attack in progress by default', () => {
    render(<TestWrapper><AttackActivity /></TestWrapper>)
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Attack in progress')
    expect(status).toHaveTextContent('Prompt, target response, evaluation')
  })

  it('should describe loading a scan', () => {
    render(<TestWrapper><AttackActivity loading /></TestWrapper>)
    expect(screen.getByRole('status')).toHaveTextContent('Loading scan')
    expect(screen.getByRole('status')).toHaveTextContent('Retrieving configuration, conversations and scores')
  })

  it('should use a caller label', () => {
    render(<TestWrapper><AttackActivity label="Waiting to start" /></TestWrapper>)
    expect(screen.getByRole('status')).toHaveTextContent('Waiting to start')
  })
})
