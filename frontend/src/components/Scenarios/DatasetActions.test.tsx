import { render, screen, within } from '@testing-library/react'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import DatasetActions from './DatasetActions'

describe('DatasetActions', () => {
  it('should open the dataset catalog in a new tab so the form keeps its edits', () => {
    render(<FluentProvider theme={webLightTheme}><DatasetActions /></FluentProvider>)

    const actions = screen.getByRole('group', { name: 'Dataset actions' })
    const browse = within(actions).getByRole('link', { name: 'Browse datasets' })
    expect(browse).toHaveAttribute('href', '/datasets')
    expect(browse).toHaveAttribute('target', '_blank')
    expect(browse).toHaveAttribute('rel', 'noopener noreferrer')
    expect(within(actions).getAllByRole('link')).toHaveLength(1)
  })
})
