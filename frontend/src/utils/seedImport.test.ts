import type { SeedRecord } from '@/types'
import { buildSeedImport } from './seedImport'

function makeSeed(overrides: Partial<SeedRecord> = {}): SeedRecord {
  return {
    id: 'seed-1',
    seed_type: 'prompt',
    value: 'Hello world',
    value_sha256: 'hash-1',
    data_type: 'text',
    name: null,
    dataset_name: 'test_dataset',
    origin: 'local',
    harm_categories: null,
    description: null,
    authors: null,
    groups: null,
    source: null,
    date_added: '2026-01-01',
    added_by: 'tester',
    metadata: null,
    prompt_group_id: null,
    sequence: 0,
    role: 'user',
    parameters: null,
    conditions: null,
    response_json_schema: null,
    ...overrides,
  }
}

describe('buildSeedImport', () => {
  it('returns unsupported for empty members', () => {
    const outcome = buildSeedImport([])
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/No stored members/)
    }
  })

  it('rejects simulated_conversation configs with clear explanation', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'cfg-1',
        seed_type: 'simulated_conversation',
        value: '{"template": "chat"}',
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/simulated conversation configuration/)
    }
  })

  it('rejects examples that contain only objective records', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'obj-1',
        seed_type: 'objective',
        value: 'Obtain admin privileges',
        role: null,
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/only objective records/)
    }
  })

  it('builds a single prompt import for a single user text prompt', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-1',
        value: 'Give me the instructions',
        role: 'user',
      }),
      makeSeed({
        id: 'obj-1',
        seed_type: 'objective',
        value: 'Safety test objective',
        role: null,
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('single_prompt')
    if (outcome.type === 'single_prompt') {
      expect(outcome.text).toBe('Give me the instructions')
      expect(outcome.attachments).toHaveLength(0)
      expect(outcome.objective).toBe('Safety test objective')
    }
  })

  it('builds a single prompt with attachments for multimodal prompt', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-text',
        data_type: 'text',
        value: 'What is in this image?',
        role: 'user',
      }),
      makeSeed({
        id: 'p-img',
        data_type: 'image_path',
        value: 'sample.png',
        value_url: 'https://storage/sample.png?token=expiring',
        role: 'user',
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('single_prompt')
    if (outcome.type === 'single_prompt') {
      expect(outcome.text).toBe('What is in this image?')
      expect(outcome.attachments).toHaveLength(1)
      expect(outcome.attachments[0].type).toBe('image')
      expect(outcome.attachments[0].sourceValue).toBe('sample.png')
      expect(outcome.attachments[0].url).toBe('https://storage/sample.png?token=expiring')
      // source_piece_id must NOT be set to seed ID
      expect(outcome.attachments[0].pieceId).toBeUndefined()
    }
  })

  it('correctly maps assistant and tool turns to simulated roles in multi-turn conversation', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-1',
        value: 'Initial user turn',
        role: 'user',
        sequence: 0,
        prompt_group_id: 'group-0',
      }),
      makeSeed({
        id: 'p-2',
        value: 'Assistant answer',
        role: 'assistant',
        sequence: 1,
        prompt_group_id: 'group-1',
      }),
      makeSeed({
        id: 'p-3',
        value: 'Follow-up user turn',
        role: 'user',
        sequence: 2,
        prompt_group_id: 'group-2',
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('conversation')
    if (outcome.type === 'conversation') {
      expect(outcome.messages).toHaveLength(3)
      expect(outcome.messages[0].role).toBe('user')
      expect(outcome.messages[1].role).toBe('simulated_assistant')
      expect(outcome.messages[2].role).toBe('user')

      // Check invariants on pieces
      for (const msg of outcome.messages) {
        for (const piece of msg.pieces) {
          expect(piece.source_piece_id).toBeUndefined()
        }
      }
    }
  })

  it('rejects ambiguous missing roles across multiple members', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-1',
        value: 'Turn 1',
        role: 'user',
      }),
      makeSeed({
        id: 'p-2',
        value: 'Turn 2 without role',
        role: null,
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/ambiguous/)
    }
  })

  it('rejects conflicting roles within the same prompt group', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-1',
        value: 'Piece 1',
        role: 'user',
        prompt_group_id: 'group-1',
      }),
      makeSeed({
        id: 'p-2',
        value: 'Piece 2',
        role: 'assistant',
        prompt_group_id: 'group-1',
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/conflicting roles/)
    }
  })

  it('groups multiple pieces in the same prompt group into one turn', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-1',
        data_type: 'text',
        value: 'Look at this file and describe it',
        role: 'user',
        sequence: 0,
        prompt_group_id: 'group-1',
      }),
      makeSeed({
        id: 'p-2',
        data_type: 'image_path',
        value: 'chart.png',
        role: 'user',
        sequence: 1,
        prompt_group_id: 'group-1',
      }),
      makeSeed({
        id: 'p-3',
        data_type: 'text',
        value: 'Sure, here is the explanation',
        role: 'assistant',
        sequence: 2,
        prompt_group_id: 'group-2',
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('conversation')
    if (outcome.type === 'conversation') {
      expect(outcome.messages).toHaveLength(2)
      expect(outcome.messages[0].role).toBe('user')
      expect(outcome.messages[0].pieces).toHaveLength(2)
      expect(outcome.messages[0].pieces[0].data_type).toBe('text')
      expect(outcome.messages[0].pieces[1].data_type).toBe('image_path')
      expect(outcome.messages[1].role).toBe('simulated_assistant')
      expect(outcome.messages[1].pieces).toHaveLength(1)
    }
  })

  it('extracts system prompt when conversation starts with a system turn', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-sys',
        data_type: 'text',
        value: 'You are a helpful assistant.',
        role: 'system',
        sequence: 0,
      }),
      makeSeed({
        id: 'p-user',
        data_type: 'text',
        value: 'Hello!',
        role: 'user',
        sequence: 1,
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('conversation')
    if (outcome.type === 'conversation') {
      expect(outcome.systemPrompt).toBe('You are a helpful assistant.')
      expect(outcome.messages).toHaveLength(2)
      expect(outcome.messages[0].role).toBe('system')
      expect(outcome.messages[1].role).toBe('user')
    }
  })

  it('rejects unrecognized role values with clear explanation', () => {
    const members: SeedRecord[] = [
      makeSeed({
        id: 'p-invalid',
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        role: 'unknown_alien_role' as any,
      }),
    ]
    const outcome = buildSeedImport(members)
    expect(outcome.type).toBe('unsupported')
    if (outcome.type === 'unsupported') {
      expect(outcome.reason).toMatch(/unsupported role/)
    }
  })
})
