import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import ScenarioTechniqueSelector from './ScenarioTechniqueSelector'

describe('ScenarioTechniqueSelector', () => {
  it('disables an unavailable technique with its own accessible explanation', async () => {
    const user = userEvent.setup()
    const onTechniquesChange = jest.fn()
    render(
      <FluentProvider theme={webLightTheme}>
        <ScenarioTechniqueSelector
          techniqueOptions={[
            { name: 'direct', description: null, tags: [] },
            { name: 'tap', description: null, tags: [], unavailable_reason: 'Requires a different scoring configuration.' },
          ]}
          selectedTechniques={['direct']}
          includeBaseline={false}
          isBaselineForbidden
          disabled={false}
          onTechniquesChange={onTechniquesChange}
          onIncludeBaselineChange={jest.fn()}
        />
      </FluentProvider>,
    )
    const tap = screen.getByRole('checkbox', { name: 'tap', exact: true })
    expect(tap).toBeDisabled()
    expect(tap).toHaveAccessibleDescription('Requires a different scoring configuration.')
    expect(screen.getByRole('checkbox', { name: 'baseline', exact: true }))
      .toHaveAccessibleDescription('This scenario does not support a baseline comparison.')
    await user.click(tap)
    expect(onTechniquesChange).not.toHaveBeenCalled()
  })
})
