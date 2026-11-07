import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'

import ScoreDetails from './ScoreDetails'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('ScoreDetails', () => {
  it('should show the value, scorer, role, categories, and rationale', () => {
    render(<TestWrapper><ScoreDetails testId="details" score={{
      scorer: 'SelfAskRefusalScorer', scoreType: 'true_false', value: 'true', status: 'complete',
      rationale: 'The target complied.', categories: ['jailbreak', '', 'harm'], isObjective: true,
    }} /></TestWrapper>)

    const details = screen.getByTestId('details')
    expect(details).toHaveTextContent('Score details')
    expect(screen.getByText('true')).toBeInTheDocument()
    expect(screen.getByText('SelfAskRefusalScorer')).toBeInTheDocument()
    expect(screen.getByText('Final score')).toBeInTheDocument()
    expect(screen.getByText('jailbreak, harm')).toBeInTheDocument()
    expect(screen.getByText('The target complied.')).toBeInTheDocument()
  })

  it('should name an undetermined score by its status and omit empty sections', () => {
    render(<TestWrapper><ScoreDetails score={{ scorer: 'Judge', scoreType: 'float_scale', status: 'undetermined' }} /></TestWrapper>)

    expect(screen.getByText('undetermined')).toBeInTheDocument()
    expect(screen.getByText('Supporting score')).toBeInTheDocument()
    expect(screen.queryByText('Category')).not.toBeInTheDocument()
    expect(screen.queryByText('Rationale')).not.toBeInTheDocument()
  })
})
