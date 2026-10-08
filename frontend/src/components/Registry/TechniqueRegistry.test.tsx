import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { convertersApi, targetsApi, techniquesApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { TechniqueInstance, TechniqueTypeResponse } from '@/types'

import TechniqueRegistry from './TechniqueRegistry'

let mockRuntime = { generation: 1, ready: true }
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: () => mockRuntime }))
jest.mock('@/services/api', () => ({
  techniquesApi: { listTechniques: jest.fn(), listTypes: jest.fn(), getTechnique: jest.fn(), createTechnique: jest.fn() },
  targetsApi: { listTargets: jest.fn() },
  convertersApi: { listConverters: jest.fn() },
}))

const techniques = jest.mocked(techniquesApi)
const targets = jest.mocked(targetsApi)
const converters = jest.mocked(convertersApi)

function technique(name: string, attackType = 'PromptSendingAttack', tags = ['basic']): TechniqueInstance {
  return { name, attack_type: attackType, tags, description: `${name} description`,
    uses_adversarial: false, uses_default_adversarial_target: false, configuration: {
      attack_args: { attempts: 0, unset: null, nested: { enabled: false, optional: null, empty: [] } },
    },
    evaluation_identifier: { class_name: 'AttackTechniqueFactory', class_module: 'pyrit.scenario.core.attack_technique_factory',
      hash: `${name}-hash`, eval_hash: `${name}-eval-hash`, name, optional: null } }
}

const metadata: TechniqueTypeResponse = {
  items: [
    { attack_type: 'PromptSendingAttack', description: 'Sends a prompt', supports_converters: true, supports_adversarial: false,
      parameters: [{ name: 'max_attempts_on_failure', type_name: 'int', required: false },
        { name: 'settings', type_name: 'Settings', required: false, variants: { basic: [
          { name: 'enabled', type_name: 'bool', required: false },
          { name: 'limit', type_name: 'int', required: true },
        ] } }] },
    { attack_type: 'RedTeamingAttack', description: 'Uses an adversarial target', parameters: [],
      supports_adversarial: true, supports_converters: true },
    { attack_type: 'ComplexAttack', description: 'Requires Python', parameters: [
      { name: 'callback', type_name: 'Callable', required: true },
    ], supports_adversarial: false, supports_converters: false },
  ],
}

function tree() {
  return <FluentProvider theme={webLightTheme}><TechniqueRegistry /></FluentProvider>
}

async function openCreate() {
  const user = userEvent.setup()
  await user.click(await screen.findByRole('button', { name: 'New technique' }))
  await screen.findByRole('combobox', { name: 'Attack type' })
  return user
}

describe('TechniqueRegistry', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    for (const method of [
      ...Object.values(techniques), ...Object.values(targets),
      ...Object.values(converters),
    ]) method.mockReset()
    mockRuntime = { generation: 1, ready: true }
    techniques.listTechniques.mockResolvedValue({ items: [technique('first'), technique('second', 'RedTeamingAttack', ['advanced'])] })
    techniques.listTypes.mockResolvedValue(metadata)
    techniques.createTechnique.mockResolvedValue(technique('created'))
    targets.listTargets.mockResolvedValue({
      items: [makeTarget({ target_registry_name: 'local' })], pagination: { limit: 200, has_more: false },
    })
    converters.listConverters.mockResolvedValue({ items: [
      { converter_id: 'b64', identifier: { class_name: 'Base64Converter', class_module: 'pyrit.converter', hash: 'b64', pyrit_version: '1' } },
      { converter_id: 'rot13', identifier: { class_name: 'ROT13Converter', class_module: 'pyrit.converter', hash: 'rot13', pyrit_version: '1' } },
    ] })
  })

  it('loads, filters, inspects safe settings, and refreshes the list', async () => {
    techniques.getTechnique.mockRejectedValue(new Error('Detail unavailable'))
    const user = userEvent.setup()
    render(tree())
    expect(screen.getByText('Loading techniques...')).toBeInTheDocument()
    await screen.findByRole('table', { name: 'Registered techniques' })
    await user.type(screen.getByRole('textbox', { name: 'Search techniques' }), 'first')
    expect(screen.queryByText('second description')).not.toBeInTheDocument()
    await user.clear(screen.getByRole('textbox', { name: 'Search techniques' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by attack type' }), 'RedTeamingAttack')
    expect(screen.queryByText('first description')).not.toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by attack type' }), '')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by tag' }), 'basic')
    await user.click(screen.getByRole('button', { name: 'Details for first' }))
    const dialog = within(screen.getByRole('dialog'))
    expect(dialog.getByRole('heading', { name: 'Configuration' })).toBeInTheDocument()
    expect(dialog.getByLabelText('Technique configuration')).toHaveTextContent('"attempts": 0')
    expect(dialog.getByLabelText('Factory evaluation identifier')).toHaveTextContent('"hash": "first-hash"')
    expect(dialog.getByLabelText('Factory evaluation identifier')).toHaveTextContent('"eval_hash": "first-eval-hash"')
    expect(dialog.getByLabelText('Factory evaluation identifier')).toHaveTextContent('"name": "first"')
    expect(dialog.queryByText(/safe display settings|This identifies the registered factory/)).not.toBeInTheDocument()
    expect(dialog.queryByRole('heading', { name: 'Factory evaluation identifier' })).not.toBeInTheDocument()
    expect(JSON.parse(dialog.getByLabelText('Technique configuration').textContent ?? '')).toEqual({
      attack_args: { attempts: 0, nested: { enabled: false, empty: [] } },
    })
    expect(dialog.getByLabelText('Factory evaluation identifier')).not.toHaveTextContent('"optional"')
    const response = await techniques.listTechniques.mock.results[0].value
    expect(response.items[0].configuration.attack_args).toHaveProperty('unset', null)
    expect(techniques.getTechnique).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Close' }))
    await user.selectOptions(await screen.findByRole('combobox', { name: 'Filter by tag' }), '')
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(techniques.listTechniques).toHaveBeenCalledTimes(2))
  })

  it('shows empty and no-match states', async () => {
    techniques.listTechniques.mockResolvedValueOnce({ items: [] })
    const user = userEvent.setup()
    render(tree())
    await screen.findByText(/No techniques registered/)
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    await screen.findByRole('table')
    await user.type(screen.getByRole('textbox', { name: 'Search techniques' }), 'missing')
    expect(screen.getByText('No techniques match these filters.')).toBeInTheDocument()
  })

  it('reports list errors with retry', async () => {
    techniques.listTechniques.mockRejectedValueOnce(new Error('List unavailable'))
    const user = userEvent.setup()
    render(tree())
    await screen.findByText('List unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry' }))
    await screen.findByRole('table')
  })

  it('creates a metadata-driven configuration with false, zero, and ordered duplicate references', async () => {
    render(tree())
    const user = await openCreate()
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'created')
    await user.type(screen.getByRole('textbox', { name: 'Description' }), 'Custom settings')
    await user.type(screen.getByRole('textbox', { name: 'Tags' }), 'mine, basic')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'PromptSendingAttack')
    await user.type(screen.getByLabelText('max_attempts_on_failure'), '0')
    await user.selectOptions(screen.getByLabelText('settings'), 'basic')
    await user.selectOptions(screen.getByLabelText('enabled'), 'false')
    await user.type(screen.getByLabelText('limit *'), '0')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Request converters' }), 'b64')
    await user.click(screen.getByRole('button', { name: 'Add to Request converters' }))
    await user.click(screen.getByRole('button', { name: 'Add to Request converters' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Request converters' }), 'rot13')
    await user.click(screen.getByRole('button', { name: 'Add to Request converters' }))
    await user.click(screen.getByRole('button', { name: 'Move Request converters 3 up' }))
    await user.selectOptions(screen.getByRole('combobox', { name: 'Response converters' }), 'rot13')
    await user.click(screen.getByRole('button', { name: 'Add to Response converters' }))
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    await waitFor(() => expect(techniques.createTechnique).toHaveBeenCalledTimes(1))
    const request = techniques.createTechnique.mock.calls[0][0]
    expect(request).toMatchObject({ name: 'created', tags: ['mine', 'basic'], type: 'PromptSendingAttack', params: {
      max_attempts_on_failure: 0, settings: { type: 'basic', parameters: { enabled: false, limit: 0 } },
    }, request_converters: ['b64', 'rot13', 'b64'], response_converters: ['rot13'] })
    expect(request.params).not.toHaveProperty('objective_target')
    await waitFor(() => expect(screen.getByRole('button', { name: 'New technique' })).toHaveFocus())
    expect(techniques.listTechniques).toHaveBeenCalledTimes(2)
  })

  it('captures optional adversarial target and inline prompts without an objective target', async () => {
    render(tree())
    const user = await openCreate()
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'adversarial')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'RedTeamingAttack')
    expect(screen.getByText(/resolve the default adversarial target at execution/)).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Adversarial target' }), 'local')
    await user.type(screen.getByRole('textbox', { name: 'Adversarial system prompt' }), 'system')
    await user.type(screen.getByRole('textbox', { name: 'Adversarial seed prompt' }), 'seed')
    await user.type(screen.getByRole('textbox', { name: 'Adversarial per-turn prompt' }), 'turn')
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    await waitFor(() => expect(techniques.createTechnique).toHaveBeenCalled())
    expect(techniques.createTechnique.mock.calls[0][0]).toMatchObject({
      adversarial_chat: 'local', adversarial_system_prompt: 'system', adversarial_seed_prompt: 'seed', adversarial_prompt_template: 'turn',
    })
  })

  it('blocks unsupported required inputs and invalid selectors', async () => {
    techniques.createTechnique.mockRejectedValueOnce(new Error('Do not use all, default, or types.'))
    render(tree())
    const user = await openCreate()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'ComplexAttack')
    expect(screen.getByText(/callback: Required input cannot be set here/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add technique' })).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'PromptSendingAttack')
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'all')
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    await screen.findByText(/Do not use all, default, or types\./)
    expect(techniques.createTechnique).toHaveBeenCalledTimes(1)
  })

  it('reports metadata failure and permits retry', async () => {
    techniques.listTypes.mockRejectedValueOnce(new Error('Metadata unavailable'))
    render(tree())
    const user = userEvent.setup()
    await user.click(screen.getByRole('button', { name: 'New technique' }))
    await screen.findByText('Metadata unavailable')
    await user.click(screen.getByRole('button', { name: 'Retry metadata' }))
    await screen.findByRole('combobox', { name: 'Attack type' })
  })

  it('reports creation errors and restores focus after cancel', async () => {
    techniques.createTechnique.mockRejectedValueOnce(new Error('Name already exists'))
    render(tree())
    const user = await openCreate()
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'first')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'PromptSendingAttack')
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    await screen.findByText('Name already exists')
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'New technique' })).toHaveFocus())
  })

  it('ignores create completion after cancel and old list responses after runtime replacement', async () => {
    let completeCreate: ((value: TechniqueInstance) => void) | undefined
    techniques.createTechnique.mockReturnValue(new Promise((resolve) => { completeCreate = resolve }))
    const { rerender } = render(tree())
    const user = await openCreate()
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'later')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'PromptSendingAttack')
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await act(async () => { completeCreate?.(technique('later')) })
    expect(techniques.listTechniques).toHaveBeenCalledTimes(1)
    let completeList: ((value: { items: TechniqueInstance[] }) => void) | undefined
    techniques.listTechniques.mockReturnValueOnce(new Promise((resolve) => { completeList = resolve }))
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    mockRuntime = { generation: 2, ready: true }
    rerender(tree())
    await waitFor(() => expect(screen.getByText('New technique', { selector: 'button' })).toHaveFocus())
    await screen.findByRole('table')
    await act(async () => { completeList?.({ items: [technique('obsolete')] }) })
    expect(screen.queryByText('obsolete description')).not.toBeInTheDocument()
  })

  it('ignores pending creation when the runtime generation changes', async () => {
    let completeCreate: ((value: TechniqueInstance) => void) | undefined
    techniques.createTechnique.mockReturnValue(new Promise((resolve) => { completeCreate = resolve }))
    const { rerender } = render(tree())
    const user = await openCreate()
    await user.type(screen.getByRole('textbox', { name: 'Registry name' }), 'previous_runtime')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Attack type' }), 'PromptSendingAttack')
    await user.click(screen.getByRole('button', { name: 'Add technique' }))
    mockRuntime = { generation: 2, ready: true }
    rerender(tree())
    await waitFor(() => expect(screen.getByText('New technique', { selector: 'button' })).toHaveFocus())
    await waitFor(() => expect(techniques.listTechniques).toHaveBeenCalledTimes(2))
    // Browser coverage checks accessibility after Tabster's modal teardown.
    await screen.findByText('first description')
    expect(screen.queryByRole('dialog', { hidden: true })).not.toBeInTheDocument()
    await act(async () => { completeCreate?.(technique('previous_runtime')) })
    expect(techniques.listTechniques).toHaveBeenCalledTimes(2)
    expect(screen.queryByText('previous_runtime description')).not.toBeInTheDocument()
  })
})
