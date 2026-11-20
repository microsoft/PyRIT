import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter, Route, Routes } from 'react-router'

import DatasetActions from './DatasetActions'

describe('DatasetActions', () => {
  it('should open the dataset catalog in the app from Browse datasets', async () => {
    const user = userEvent.setup()
    render(<FluentProvider theme={webLightTheme}><MemoryRouter initialEntries={['/scanner/launch/scenario']}>
      <Routes>
        <Route path="/scanner/launch/:scenarioName" element={<DatasetActions />} />
        <Route path="/datasets" element={<p>Dataset catalog</p>} />
      </Routes>
    </MemoryRouter></FluentProvider>)

    const actions = screen.getByRole('group', { name: 'Dataset actions' })
    const browse = within(actions).getByRole('link', { name: 'Browse datasets' })
    expect(browse).toHaveAttribute('href', '/datasets')
    expect(within(actions).getAllByRole('link')).toHaveLength(1)

    await user.click(browse)
    expect(await screen.findByText('Dataset catalog')).toBeInTheDocument()
  })
})
