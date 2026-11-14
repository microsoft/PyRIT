import type { ReactNode } from 'react'

import { FluentProvider, Text, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'

import DetailFacts, { DetailFact } from './DetailFacts'

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('DetailFacts', () => {
  it('should pair each label with its value in a named description list', () => {
    render(<TestWrapper>
      <DetailFacts label="Attack outcome">
        <DetailFact label="Objective:" tone="primary">Extract the system prompt</DetailFact>
        <DetailFact label="Technique"><Text>Crescendo</Text></DetailFact>
      </DetailFacts>
    </TestWrapper>)

    const list = screen.getByLabelText('Attack outcome')
    expect(list.tagName).toBe('DL')
    expect(within(list).getAllByRole('term').map((term) => term.textContent)).toEqual(['Objective:', 'Technique'])
    expect(within(list).getAllByRole('definition').map((value) => value.textContent)).toEqual([
      'Extract the system prompt', 'Crescendo',
    ])
  })
})
