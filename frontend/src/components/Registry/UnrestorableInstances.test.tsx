import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { UnrestorableInstance } from '@/types'

import UnrestorableInstances from './UnrestorableInstances'

const READABLE: UnrestorableInstance = {
  kind: 'target',
  name: 'team-chat',
  type: 'OpenAIChatTarget',
  reason: "Environment variable 'TEAM_CHAT_KEY' (for 'api_key') is not set.",
  version: 'abc123',
}
const UNREADABLE: UnrestorableInstance = {
  kind: 'target',
  name: 'target_locked_0123456789ab',
  reason: 'The saved document could not be read from storage: access denied.',
  version: null,
}

function renderInstances(instances: UnrestorableInstance[], onDelete = jest.fn()) {
  render(
    <FluentProvider theme={webLightTheme}>
      <UnrestorableInstances noun="target" instances={instances} restoreError={null} onDelete={onDelete} />
    </FluentProvider>,
  )
  return onDelete
}

describe('UnrestorableInstances', () => {
  it('offers to delete a saved instance whose document was read', async () => {
    const user = userEvent.setup()
    const onDelete = renderInstances([READABLE])

    await user.click(screen.getByRole('button', { name: 'Delete saved target team-chat' }))

    expect(onDelete).toHaveBeenCalledWith(expect.anything(), READABLE)
  })

  it('explains a document storage could not return without offering a delete', () => {
    renderInstances([UNREADABLE])

    expect(screen.getByText(UNREADABLE.reason)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /delete saved target/i })).not.toBeInTheDocument()
  })
})
