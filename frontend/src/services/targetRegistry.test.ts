import { targetsApi } from '@/services/api'
import { makeTarget } from '@/test-utils/targetFixtures'
import { buildTemperatureTarget } from './targetRegistry'

jest.mock('@/services/api', () => ({ targetsApi: { buildTarget: jest.fn() } }))

describe('buildTemperatureTarget', () => {
  it('pins the private target capabilities rather than the changing registry view', async () => {
    const capabilities = {
      supports_multi_turn: true, supports_system_prompt: true,
      supports_json_output: false, supports_json_schema: false,
      supported_input_modalities: ['text'], supported_output_modalities: ['text'],
    }
    const source = {
      ...makeTarget({ target_registry_name: 'source', capabilities }),
      supports_temperature_override: true,
    }
    const built = {
      identifier: { ...source.identifier, hash: 'private-hash' },
      capabilities: { ...capabilities, supports_multi_turn: false, input_modalities: [['text']] },
    }
    jest.mocked(targetsApi.buildTarget).mockResolvedValue(built)
    const privateTarget = await buildTemperatureTarget(source, 0.7)
    expect(privateTarget.binding?.capabilities).toEqual(built.capabilities)
    expect(privateTarget.binding?.effective_hash).toBe('private-hash')
    expect(source.capabilities).toEqual(capabilities)
  })
})
