import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { scorersApi } from '@/services/api'
import type { ScorerInstance } from '@/types'

import CreateScorerDialog from './CreateScorerDialog'

jest.mock('@/services/api', () => ({
  scorersApi: { listTypes: jest.fn(), createScorer: jest.fn() },
}))

const created: ScorerInstance = {
  scorer_registry_name: 'offline',
  identifier: { class_name: 'SubStringScorer', hash: 'offline-hash', scorer_type: 'true_false' },
}
const onCreated = jest.fn()
const onClose = jest.fn()

function renderDialog() {
  return render(<FluentProvider theme={webLightTheme}>
    <CreateScorerDialog targets={[]} scorers={[]} onCreated={onCreated} onClose={onClose} />
  </FluentProvider>)
}

beforeEach(() => {
  jest.clearAllMocks()
  jest.mocked(scorersApi.listTypes).mockResolvedValue({ items: [{
    scorer_type: 'SubStringScorer', supports_objective_scoring: true,
    parameters: [{ name: 'substring', type_name: 'str', required: true, default: null }],
  }, {
    scorer_type: 'FloatOnly', supports_objective_scoring: false, parameters: [],
  }] })
  jest.mocked(scorersApi.createScorer).mockResolvedValue(created)
})

describe('CreateScorerDialog', () => {
  it('creates a registered deterministic scorer without requesting a target', async () => {
    const user = userEvent.setup()
    renderDialog()
    await screen.findByRole('option', { name: 'SubStringScorer' })
    expect(screen.queryByRole('option', { name: 'FloatOnly' })).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: 'Scorer name' }), 'offline')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Scorer type' }), 'SubStringScorer')
    await user.type(screen.getByRole('textbox', { name: 'substring', exact: true }), 'Offline browser')
    await user.click(screen.getByRole('button', { name: 'Create scorer', exact: true }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created))
    expect(scorersApi.createScorer).toHaveBeenCalledWith({
      name: 'offline', type: 'SubStringScorer', params: { substring: 'Offline browser' },
    })
  })

  it('retains the draft and reports failed creation without returning success', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.createScorer).mockRejectedValue(new Error('Name already exists.'))
    renderDialog()
    await screen.findByRole('option', { name: 'SubStringScorer' })
    await user.type(screen.getByRole('textbox', { name: 'Scorer name' }), 'offline')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Scorer type' }), 'SubStringScorer')
    await user.type(screen.getByRole('textbox', { name: 'substring', exact: true }), 'retained')
    await user.click(screen.getByRole('button', { name: 'Create scorer', exact: true }))
    expect(await screen.findByText('Name already exists.')).toBeVisible()
    expect(screen.getByRole('textbox', { name: 'substring', exact: true })).toHaveValue('retained')
    expect(onCreated).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows unsupported required configuration instead of attempting an invalid scorer', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.listTypes).mockResolvedValue({ items: [{
      scorer_type: 'CustomScorer', supports_objective_scoring: true,
      parameters: [{ name: 'callback', type_name: 'Callable', required: true, default: null }],
    }] })
    renderDialog()
    await screen.findByRole('option', { name: 'CustomScorer' })
    await user.type(screen.getByRole('textbox', { name: 'Scorer name' }), 'custom')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Scorer type' }), 'CustomScorer')
    expect(screen.getByRole('alert')).toHaveTextContent('Configure callback in Python')
    expect(screen.getByRole('button', { name: 'Create scorer', exact: true })).toBeDisabled()
    expect(scorersApi.createScorer).not.toHaveBeenCalled()
  })

  it('reports an unavailable scorer catalog', async () => {
    jest.mocked(scorersApi.listTypes).mockRejectedValue(new Error('Registry is unavailable.'))
    renderDialog()
    expect(await screen.findByText('Registry is unavailable.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Create scorer', exact: true })).toBeDisabled()
  })
})
