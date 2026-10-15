import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { SeedExampleDetailResponse, SeedRecord } from '@/types'

import SeedExampleDetail from './SeedExampleDetail'

function member(overrides: Partial<SeedRecord> = {}): SeedRecord {
  return {
    id: 'member-1',
    seed_type: 'prompt',
    value: 'plain',
    value_sha256: 'abc',
    data_type: 'text',
    name: 'named',
    dataset_name: 'harmbench',
    origin: 'local',
    harm_categories: ['hate'],
    description: 'described',
    authors: ['ada'],
    groups: ['group'],
    source: 'source',
    date_added: '2026-01-02T03:04:05Z',
    added_by: 'tester',
    metadata: { generation_conversation_id: 'conversation-9' },
    prompt_group_id: 'group-1',
    sequence: 0,
    role: 'user',
    parameters: ['name'],
    conditions: [{ condition_type: 'text_match', pattern: 'secret' }],
    response_json_schema: { type: 'object' },
    ...overrides,
  }
}

const EXACT_VALUE = '  keep\n\nwhitespace  '
const CONFIG_VALUE = '{\n  "num_turns": 3\n}'

const DETAIL: SeedExampleDetailResponse = {
  example_id: 'example-1',
  name: 'multimodal',
  preview: 'short',
  preview_truncated: true,
  modalities: ['text', 'image_path'],
  seed_types: ['prompt', 'simulated_conversation'],
  piece_count: 2,
  objective_count: 0,
  harm_categories: ['hate'],
  has_unlabeled_harm: false,
  members: [
    member({
      id: 'prompt-1',
      seed_type: 'prompt',
      value: EXACT_VALUE,
      data_type: 'text',
    }),
    member({
      id: 'config-1',
      seed_type: 'simulated_conversation',
      value: CONFIG_VALUE,
      data_type: 'text',
      parameters: null,
      conditions: null,
      harm_categories: null,
      role: null,
      sequence: null,
      value_sha256: null,
      name: null,
      dataset_name: null,
      description: null,
      authors: null,
      groups: null,
      source: null,
      metadata: null,
      prompt_group_id: null,
      response_json_schema: null,
      origin: 'unknown',
    }),
    member({
      id: 'image-1',
      seed_type: 'prompt',
      value: '<img src=x onerror=alert(1)> /tmp/secret.png',
      data_type: 'image_path',
      harm_categories: [],
      parameters: [],
      conditions: [],
    }),
  ],
}

function renderDetail(detail: SeedExampleDetailResponse = DETAIL) {
  return render(
    <FluentProvider theme={webLightTheme}>
      <SeedExampleDetail detail={detail} />
    </FluentProvider>,
  )
}

describe('SeedExampleDetail', () => {
  afterEach(() => {
    jest.restoreAllMocks()
  })

  it('keeps server member order and shows stored fields without executing them', () => {
    renderDetail()

    const members = screen.getAllByRole('article')
    expect(members.map((item) => item.getAttribute('aria-label'))).toEqual([
      'Member 1',
      'Member 2',
      'Member 3',
    ])
    expect(within(members[0]).getByTestId('seed-value-prompt-1').textContent).toBe(EXACT_VALUE)
    expect(within(members[1]).getByText('Stored configuration. This view does not run it.')).toBeInTheDocument()
    expect(within(members[1]).getByTestId('seed-value-config-1').textContent).toBe(CONFIG_VALUE)
    expect(screen.queryByText('Num turns')).not.toBeInTheDocument()
    expect(within(members[2]).getByText('Media is not played in this view.')).toBeInTheDocument()
    expect(document.querySelector('img')).toBeNull()
    expect(document.querySelector('audio')).toBeNull()
    expect(document.querySelector('video')).toBeNull()
    expect(screen.getAllByText('Template status is not stored.').length).toBeGreaterThan(0)
    expect(screen.queryByText(/this is a template/i)).not.toBeInTheDocument()
    expect(screen.queryByText('Safe')).not.toBeInTheDocument()
    expect(screen.queryByText('License')).not.toBeInTheDocument()
    expect(within(members[0]).getByText('text_match')).toBeInTheDocument()
    expect(within(members[0]).getByText('conversation-9')).toBeInTheDocument()
    expect(within(members[0]).getByText('name')).toBeInTheDocument()
    expect(within(members[1]).getAllByText('Missing').length).toBeGreaterThan(0)
    expect(within(members[2]).getAllByText('None').length).toBeGreaterThan(0)
  })

  it('copies the stored value exactly and reports a clipboard failure', async () => {
    const user = userEvent.setup()
    const writeText = jest.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
    renderDetail()

    await user.click(screen.getAllByRole('button', { name: 'Copy full text' })[0])
    expect(writeText).toHaveBeenCalledWith(EXACT_VALUE)
    expect(screen.getByRole('button', { name: 'Copied full text' })).toBeInTheDocument()

    writeText.mockRejectedValueOnce(new Error('denied'))
    await user.click(screen.getAllByRole('button', { name: 'Copy ID' })[0])
    expect(writeText).toHaveBeenCalledWith('prompt-1')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not copy to the clipboard.')
  })
})
