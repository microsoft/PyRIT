import { makeTarget } from '@/test-utils/targetFixtures'

import { isGuiTarget, isGuiTargetType } from './targetVisibility'

describe('GUI target visibility', () => {
  it('excludes TextTarget by implementation, not registry name or authentication', () => {
    expect(isGuiTargetType('TextTarget')).toBe(false)
    expect(isGuiTarget(makeTarget({ target_registry_name: 'demo', target_type: 'TextTarget' }))).toBe(false)
    expect(isGuiTarget(makeTarget({ target_registry_name: 'TextTarget', target_type: 'OpenAIChatTarget' }))).toBe(true)
    expect(isGuiTargetType('CredentialFreeTarget')).toBe(true)
  })

  it('excludes composites containing an SDK-only target', () => {
    const target = makeTarget({
      target_registry_name: 'demo_pool',
      target_type: 'RoundRobinTarget',
      inner_targets: [
        { target_registry_name: 'endpoint', target_type: 'OpenAIChatTarget' },
        { target_registry_name: 'demo', target_type: 'TextTarget' },
      ],
    })
    expect(isGuiTarget(target)).toBe(false)
  })

  it('keeps composites of supported targets', () => {
    expect(isGuiTarget(makeTarget({
      target_registry_name: 'pool',
      target_type: 'RoundRobinTarget',
      inner_targets: [
        { target_registry_name: 'first', target_type: 'OpenAIChatTarget' },
        { target_registry_name: 'second', target_type: 'OpenAIChatTarget' },
      ],
    }))).toBe(true)
  })
})
