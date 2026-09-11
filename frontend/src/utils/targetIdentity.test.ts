import { makeTarget } from '../test-utils/targetFixtures'
import {
  targetEndpoint,
  targetIdentifierHash,
  targetInfoMatchesTarget,
  targetModelName,
  resolveTargetByIdentifierHash,
  targetType,
  targetUnderlyingModelName,
} from './targetIdentity'

describe('targetIdentity', () => {
  describe('targetType', () => {
    it('returns the identifier class name', () => {
      const target = makeTarget({ target_registry_name: 'openai_1', target_type: 'OpenAIChatTarget' })
      expect(targetType(target)).toBe('OpenAIChatTarget')
    })
  })

  describe('targetEndpoint', () => {
    it('returns the endpoint when present', () => {
      const target = makeTarget({ target_registry_name: 'openai_1', endpoint: 'https://example.openai.azure.com' })
      expect(targetEndpoint(target)).toBe('https://example.openai.azure.com')
    })

    it('returns null when the endpoint is absent', () => {
      const target = makeTarget({ target_registry_name: 'text_1' })
      expect(targetEndpoint(target)).toBeNull()
    })
  })

  describe('targetIdentifierHash', () => {
    it('returns the identifier hash', () => {
      const target = makeTarget({ target_registry_name: 'text_1', identifier_hash: 'abc123' })
      expect(targetIdentifierHash(target)).toBe('abc123')
    })
  })

  describe('targetInfoMatchesTarget', () => {
    it('matches a historical Round Robin target by its identifier hash', () => {
      const target = makeTarget({
        target_registry_name: 'round_robin_1',
        target_type: 'RoundRobinTarget',
        endpoint: null,
        model_name: null,
        identifier_hash: 'round-robin-hash',
        inner_targets: [
          { target_registry_name: 'inner_a', model_name: 'e2e-dummy-model' },
          { target_registry_name: 'inner_b', model_name: 'e2e-dummy-model' },
        ],
      })

      expect(targetModelName(target)).toBe('e2e-dummy-model')
      expect(
        targetInfoMatchesTarget(
          {
            target_type: 'RoundRobinTarget',
            endpoint: null,
            model_name: null,
            identifier_hash: 'round-robin-hash',
          },
          target,
        ),
      ).toBe(true)
    })

    it('rejects a different composite with the same root projection', () => {
      const target = makeTarget({
        target_registry_name: 'round_robin_1',
        target_type: 'RoundRobinTarget',
        endpoint: null,
        model_name: null,
        identifier_hash: 'active-round-robin-hash',
        inner_targets: [
          { target_registry_name: 'inner_a', model_name: 'e2e-dummy-model' },
          { target_registry_name: 'inner_b', model_name: 'e2e-dummy-model' },
        ],
      })

      expect(
        targetInfoMatchesTarget(
          {
            target_type: 'RoundRobinTarget',
            endpoint: null,
            model_name: null,
            identifier_hash: 'different-round-robin-hash',
          },
          target,
        ),
      ).toBe(false)
    })
  })

  describe('resolveTargetByIdentifierHash', () => {
    it('resolves only one exact full-hash match', () => {
      const nearDuplicate = makeTarget({
        target_registry_name: 'near_duplicate',
        target_type: 'OpenAIChatTarget',
        endpoint: 'https://example.test',
        model_name: 'gpt-test',
        identifier_hash: 'full-hash-1234-near',
      })
      const exact = makeTarget({
        target_registry_name: 'exact',
        target_type: 'OpenAIChatTarget',
        endpoint: 'https://example.test',
        model_name: 'gpt-test',
        identifier_hash: 'full-hash-1234',
      })

      expect(resolveTargetByIdentifierHash('full-hash-1234', [nearDuplicate, exact])).toEqual({
        status: 'resolved',
        target: exact,
      })
    })

    it('reports duplicate registry identities as ambiguous', () => {
      const first = makeTarget({
        target_registry_name: 'duplicate_a',
        identifier_hash: 'same-full-hash',
      })
      const second = makeTarget({
        target_registry_name: 'duplicate_b',
        identifier_hash: 'same-full-hash',
      })

      expect(resolveTargetByIdentifierHash('same-full-hash', [first, second])).toEqual({
        status: 'ambiguous',
      })
    })

    it('does not resolve a Round Robin attack to an inner target', () => {
      const composite = makeTarget({
        target_registry_name: 'round_robin',
        target_type: 'RoundRobinTarget',
        identifier_hash: 'round-robin-root-hash',
        inner_targets: [
          {
            target_registry_name: 'inner',
            identifier_hash: 'inner-target-hash',
          },
        ],
      })

      expect(resolveTargetByIdentifierHash('inner-target-hash', [composite])).toEqual({
        status: 'unavailable',
      })
      expect(resolveTargetByIdentifierHash('round-robin-root-hash', [composite])).toEqual({
        status: 'resolved',
        target: composite,
      })
    })
  })

  describe('targetModelName / targetUnderlyingModelName', () => {
    it('returns the target own model name when set', () => {
      const target = makeTarget({
        target_registry_name: 'openai_1',
        model_name: 'gpt-4o-deployment',
        underlying_model_name: 'gpt-4o',
      })
      expect(targetModelName(target)).toBe('gpt-4o-deployment')
      expect(targetUnderlyingModelName(target)).toBe('gpt-4o')
    })

    it('returns null when neither the target nor inner targets provide a value', () => {
      const target = makeTarget({ target_registry_name: 'text_1' })
      expect(targetModelName(target)).toBeNull()
      expect(targetUnderlyingModelName(target)).toBeNull()
    })

    it('hoists a shared value from inner targets when they all agree', () => {
      const composite = makeTarget({
        target_registry_name: 'round_robin_1',
        target_type: 'RoundRobinTarget',
        inner_targets: [
          { target_registry_name: 'inner_a', model_name: 'gpt-4o', underlying_model_name: 'gpt-4o' },
          { target_registry_name: 'inner_b', model_name: 'gpt-4o', underlying_model_name: 'gpt-4o' },
        ],
      })
      expect(targetModelName(composite)).toBe('gpt-4o')
      expect(targetUnderlyingModelName(composite)).toBe('gpt-4o')
    })

    it('returns null when inner targets disagree', () => {
      const composite = makeTarget({
        target_registry_name: 'round_robin_1',
        target_type: 'RoundRobinTarget',
        inner_targets: [
          { target_registry_name: 'inner_a', model_name: 'gpt-4o' },
          { target_registry_name: 'inner_b', model_name: 'gpt-4o-mini' },
        ],
      })
      expect(targetModelName(composite)).toBeNull()
    })

    it('prefers the target own value over inner targets', () => {
      const composite = makeTarget({
        target_registry_name: 'round_robin_1',
        target_type: 'RoundRobinTarget',
        model_name: 'explicit',
        inner_targets: [{ target_registry_name: 'inner_a', model_name: 'gpt-4o' }],
      })
      expect(targetModelName(composite)).toBe('explicit')
    })
  })
})
