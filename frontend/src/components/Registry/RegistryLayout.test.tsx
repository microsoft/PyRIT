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
          </Route>
        </Routes>
      </MemoryRouter>
    </FluentProvider>,
  )
}

describe('RegistryLayout', () => {
  it('shows target and converter registry tabs', () => {
    renderLayout()

    expect(screen.getByRole('tab', { name: 'Targets' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'false')
    expect(screen.getByText('Target registry content')).toBeInTheDocument()
  })

  it('navigates between registry sections', async () => {
    const user = userEvent.setup()
    renderLayout()

    await user.click(screen.getByRole('tab', { name: 'Converters' }))

    expect(screen.getByRole('tab', { name: 'Converters' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('Converter registry content')).toBeInTheDocument()
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
