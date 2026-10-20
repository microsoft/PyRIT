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
            <Route path="scenario-suites" element={<div>Suite library content</div>} />
            <Route path="scenario-suites/new" element={<div>Suite editor content</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('RegistryLayout', () => {
  it('shows target, converter, preset, and suite registry tabs', () => {
    renderLayout()

    expect(screen.getByRole('tab', { name: 'Targets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Scenario presets' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByRole('tab', { name: 'Suites' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByText('Target registry content')).toBeInTheDocument()
  })

  it('navigates between registry sections', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('tab', { name: 'Converters' }))

    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Converter registry content')).toBeInTheDocument()
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

  it('opens the suite library and keeps the tab selected on nested routes', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('tab', { name: 'Suites' }))

    expect(screen.getByRole('tab', { name: 'Suites' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Suite library content')).toBeInTheDocument()
  })

  it('keeps the suite tab selected on the new-suite route', () => {
    renderLayout('/registry/scenario-suites/new')

    expect(screen.getByRole('tab', { name: 'Suites' })).toHaveAttribute('aria-selected', 'true')
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
