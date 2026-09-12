import { CompatibilityStore, isCompatibilityId } from './compatibility'

const bundledId = `1.2.0.dev0+g${'a'.repeat(40)}`

describe('compatibility identity', () => {
  it.each([
    undefined, null, 42, '', '1.2.0', `1.2.0+g${'a'.repeat(7)}`,
    `1.2.0+g${'A'.repeat(40)}`, `1.2.0+g${'a'.repeat(41)}`,
    `1.2.0+g${'a'.repeat(40)}-dirty`, ` ${bundledId}`, `${bundledId}\n`,
    `1+g${'a'.repeat(40)}`, `1.2.0+local+g${'a'.repeat(40)}`, `1.2.0${'a'.repeat(220)}+g${'a'.repeat(40)}`,
    `1.2.0junk+g${'a'.repeat(40)}`, `1.2.0.dev+g${'a'.repeat(40)}`, `1.2.0RC1+g${'a'.repeat(40)}`,
    `${bundledId}\r`, `${bundledId}\r\n`,
  ])('rejects malformed identity %p', (identity) => {
    expect(isCompatibilityId(identity)).toBe(false)
  })

  it('accepts version plus full lowercase commit', () => {
    expect(isCompatibilityId(bundledId)).toBe(true)
  })

  it.each(['1.2.0', '1.2.0a1', '1.2.0b2', '1.2.0rc3', '1.2.0.post1', '1.2.0rc1.post2.dev3'])(
    'accepts normalized version %s', (version) => {
      expect(isCompatibilityId(`${version}+g${'a'.repeat(40)}`)).toBe(true)
    },
  )
})

describe('CompatibilityStore', () => {
  it('fails closed when the bundle stamp is absent or invalid', async () => {
    const store = new CompatibilityStore('')
    const fetchVersion = jest.fn()
    expect(() => store.assertReady()).toThrow()
    await store.verify(fetchVersion)
    expect(store.getSnapshot().status).toBe('blocked')
    expect(fetchVersion).not.toHaveBeenCalled()
  })

  it.each([undefined, null, {}, 'html', { compatibility_id: 4 }, { compatibility_id: '' }])(
    'blocks malformed version response %p', async (version) => {
      const store = new CompatibilityStore(bundledId)
      await store.verify(async () => version)
      expect(store.getSnapshot().status).toBe('blocked')
      expect(() => store.assertReady()).toThrow()
    },
  )

  it.each([`1.2.0.dev1+g${'a'.repeat(40)}`, `1.2.0.dev0+g${'b'.repeat(40)}`])(
    'requires exact version and commit match: %s', async (backendId) => {
      const store = new CompatibilityStore(bundledId)
      await store.verify(async () => ({ compatibility_id: backendId }))
      expect(store.getSnapshot()).toMatchObject({ status: 'blocked', expected: backendId, actual: bundledId })
    },
  )

  it('coalesces handshakes and cannot recover after invalidation', async () => {
    const store = new CompatibilityStore(bundledId)
    const listener = jest.fn()
    const unsubscribe = store.subscribe(listener)
    const fetchVersion = jest.fn(async () => ({ compatibility_id: bundledId }))
    const handshake = store.verify(fetchVersion)
    expect(store.verify(fetchVersion)).toBe(handshake)
    await handshake
    expect(store.getSnapshot().status).toBe('ready')
    expect(() => store.assertReady()).not.toThrow()
    expect(listener).toHaveBeenCalledTimes(1)
    store.block('Deployment changed', 'expected', bundledId)
    store.block('Another problem')
    await store.verify(fetchVersion)
    expect(store.getSnapshot()).toMatchObject({ status: 'blocked', reason: 'Deployment changed' })
    expect(fetchVersion).toHaveBeenCalledTimes(1)
    expect(listener).toHaveBeenCalledTimes(2)
    unsubscribe()
  })

  it('does not let a late successful handshake clear a block', async () => {
    const store = new CompatibilityStore(bundledId)
    let resolveVersion!: (version: unknown) => void
    const handshake = store.verify(() => new Promise((resolve) => { resolveVersion = resolve }))
    store.block('Backend rejected identity')
    resolveVersion({ compatibility_id: bundledId })
    await handshake
    expect(store.getSnapshot().status).toBe('blocked')
  })

  it('blocks a failed authenticated handshake', async () => {
    const store = new CompatibilityStore(bundledId)
    await store.verify(async () => { throw new Error('401') })
    expect(store.getSnapshot().status).toBe('blocked')
  })
})
