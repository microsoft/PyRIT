import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes, useNavigate } from 'react-router'

import RegistryLayout from './RegistryLayout'

function HistoryControls() {
  const navigate = useNavigate()
  return <><button onClick={() => navigate(-1)}>Back</button><button onClick={() => navigate(1)}>Forward</button></>
}

function renderLayout(initialPath = '/registry/targets') {
  return render(
    <FluentProvider theme={webLightTheme}>
      <MemoryRouter initialEntries={[initialPath]}>
        <HistoryControls />
        <Routes>
          <Route path="/registry" element={<RegistryLayout />}>
            <Route path="targets" element={<div>Target registry content</div>} />
            <Route path="converters" element={<div>Converter registry content</div>} />
            <Route path="techniques" element={<div>Technique registry content</div>} />
            <Route path="scenario-presets" element={<div>Preset library content</div>} />
            <Route path="scenario-presets/new" element={<div>Preset editor content</div>} />
            <Route path="scorers" element={<div>Scorer registry content</div>} />
            <Route path="scenario-suites/new" element={<div>Suite editor content</div>} />
            <Route path="scenario-suites/:suiteName/edit" element={<div>Suite editor content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('RegistryLayout', () => {
  it('shows target, converter, and scenario preset registry tabs', () => {
    renderLayout()

    expect(screen.getByRole('tab', { name: 'Targets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByText('Target registry content')).toBeInTheDocument()
  })

  it('has no separate suites tab because suites share the scenario preset page', () => {
    renderLayout()

    expect(screen.queryByRole('tab', { name: 'Suites' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('tab')).toEqual(
      ['Targets', 'Converters', 'Scorers', 'Techniques', 'Scenario presets'].map(
        (name: string) => screen.getByRole('tab', { name }),
      ),
    )
  })

  it('selects the Targets tab on the bare registry route', () => {
    renderLayout('/registry')

    expect(screen.getByRole('tab', { name: 'Targets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'false')
  })

  it('navigates between registry sections', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('tab', { name: 'Converters' }))

    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Converter registry content')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Scorers' }))
    expect(screen.getByRole('tab', { name: 'Scorers' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Scorer registry content')).toBeInTheDocument()
  })

  it('opens the preset library from its tab', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('tab', { name: 'Scenario presets' }))

    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Preset library content')).toBeInTheDocument()
  })

  it('keeps the preset tab selected on a nested preset route', () => {
    renderLayout('/registry/scenario-presets/new')

    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Preset editor content')).toBeInTheDocument()
  })

  it.each([
    '/registry/scenario-suites/new',
    '/registry/scenario-suites/nightly_suite/edit',
  ])('keeps the scenario presets tab selected on suite editor route %s', (path: string) => {
    renderLayout(path)

    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Suite editor content')).toBeInTheDocument()
  })

  it('supports the Techniques direct URL and browser history', async () => {
    const user = userEvent.setup()
    renderLayout('/registry/techniques')
    expect(screen.getByRole('tab', { name: 'Techniques' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Technique registry content')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Targets' }))
    await user.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('tab', { name: 'Techniques' })).toHaveAttribute('aria-selected', 'true')
    await user.click(screen.getByRole('button', { name: 'Forward' }))
    expect(screen.getByRole('tab', { name: 'Targets' })).toHaveAttribute('aria-selected', 'true')
  })
})
