// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for `createBroadcastChannelLockManager` — the real cross-tab
 * advisory lock implementation per 01 §9.4.3 / 03 §10.4.
 *
 * Uses the `broadcast-channel` polyfill (`simulate` mode, registered as a
 * global in setupTests.ts) so two `LockManager` instances in the same
 * jest process talk to each other through the same channel — the same
 * way two browser tabs talk through the native `BroadcastChannel`.
 *
 * Test surface:
 *   1. single-instance acquire/release lifecycle
 *   2. two-instance contention (busy on second acquire; release wakes
 *      the second's next acquire)
 *   3. holderTabId on busy reply carries the holder's tabId
 *   4. per-tree isolation (different trees do not conflict)
 *   5. same-tab reacquire is idempotent (no message round-trip)
 *   6. BroadcastChannel absent → degrades to always-acquired + warn-once
 *   7. close() removes the channel listener (no further busy replies)
 *
 * Tests use a short acquireTimeoutMs (5–10ms) for fast feedback. The
 * polyfill's `postMessage` is async, so we await each acquire result and
 * use `waitFor`-style assertions where ordering is fragile.
 */

import { createBroadcastChannelLockManager } from './crossTabLock'
import { treeId } from './testHelpers'

// Unique channel name per test so parallel describe blocks don't share state.
// `simulate` mode keeps the transport in-process, but per-channel state is
// shared across all instances using the same name — including across tests
// in the same file. Using a per-test name avoids leak between tests.
let channelCounter = 0
function nextChannelName(): string {
  return `pyrit-runner-test-${++channelCounter}-${Date.now()}`
}

async function settle(): Promise<void> {
  // Two microtask hops — the polyfill posts asynchronously, and the
  // request handler's response also asynchronously. Most operations
  // need both.
  await Promise.resolve()
  await Promise.resolve()
}

// ============================================================================
// 1. Single-instance lifecycle
// ============================================================================

describe('createBroadcastChannelLockManager — single instance', () => {
  it('acquire on a fresh channel returns { acquired: true }', async () => {
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      acquireTimeoutMs: 10,
    })
    try {
      const result = await mgr.acquire(treeId('t-1'))
      expect(result).toEqual({ acquired: true })
    } finally {
      mgr.close()
    }
  })

  it('release is idempotent (no throw on second release)', async () => {
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      acquireTimeoutMs: 10,
    })
    try {
      await mgr.acquire(treeId('t-1'))
      mgr.release(treeId('t-1'))
      expect(() => mgr.release(treeId('t-1'))).not.toThrow()
    } finally {
      mgr.close()
    }
  })

  it('release on a never-acquired tree is a no-op', () => {
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      acquireTimeoutMs: 10,
    })
    try {
      expect(() => mgr.release(treeId('t-never'))).not.toThrow()
    } finally {
      mgr.close()
    }
  })
})

// ============================================================================
// 2. Two-instance contention
// ============================================================================

describe('createBroadcastChannelLockManager — two-instance contention', () => {
  it('second instance gets { acquired: false } when first holds the lock', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    const b = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-B',
      acquireTimeoutMs: 20,
    })
    try {
      const ra = await a.acquire(treeId('t-1'))
      expect(ra.acquired).toBe(true)

      const rb = await b.acquire(treeId('t-1'))
      expect(rb.acquired).toBe(false)
      if (!rb.acquired) {
        expect(rb.holderTabId).toBe('tab-A')
      }
    } finally {
      a.close()
      b.close()
    }
  })

  it('after A releases, B can acquire the same tree', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    const b = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-B',
      acquireTimeoutMs: 20,
    })
    try {
      await a.acquire(treeId('t-1'))
      const rb1 = await b.acquire(treeId('t-1'))
      expect(rb1.acquired).toBe(false)

      a.release(treeId('t-1'))
      // Give the lock_released message time to drain so A no longer
      // responds as the holder on B's next acquire attempt.
      await settle()

      const rb2 = await b.acquire(treeId('t-1'))
      expect(rb2.acquired).toBe(true)
    } finally {
      a.close()
      b.close()
    }
  })

  it('different trees do not conflict (A holds t-1, B acquires t-2)', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    const b = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-B',
      acquireTimeoutMs: 20,
    })
    try {
      const ra = await a.acquire(treeId('t-1'))
      const rb = await b.acquire(treeId('t-2'))
      expect(ra.acquired).toBe(true)
      expect(rb.acquired).toBe(true)
    } finally {
      a.close()
      b.close()
    }
  })

  it('three-way contention: A holds t-1; B and C both get busy with A as holder', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    const b = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-B',
      acquireTimeoutMs: 20,
    })
    const c = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-C',
      acquireTimeoutMs: 20,
    })
    try {
      await a.acquire(treeId('t-1'))
      const [rb, rc] = await Promise.all([b.acquire(treeId('t-1')), c.acquire(treeId('t-1'))])
      expect(rb.acquired).toBe(false)
      expect(rc.acquired).toBe(false)
      if (!rb.acquired) expect(rb.holderTabId).toBe('tab-A')
      if (!rc.acquired) expect(rc.holderTabId).toBe('tab-A')
    } finally {
      a.close()
      b.close()
      c.close()
    }
  })
})

// ============================================================================
// 3. Same-tab reacquire is idempotent
// ============================================================================

describe('createBroadcastChannelLockManager — same-tab reacquire', () => {
  it('reacquiring a lock this tab already holds returns acquired immediately', async () => {
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    try {
      const r1 = await mgr.acquire(treeId('t-1'))
      expect(r1.acquired).toBe(true)

      const start = Date.now()
      const r2 = await mgr.acquire(treeId('t-1'))
      const elapsed = Date.now() - start

      expect(r2.acquired).toBe(true)
      // Reacquire is short-circuited; no 20ms timeout round-trip.
      expect(elapsed).toBeLessThan(15)
    } finally {
      mgr.close()
    }
  })
})

// ============================================================================
// 4. BroadcastChannel absence — graceful degradation
// ============================================================================

describe('createBroadcastChannelLockManager — BroadcastChannel absent', () => {
  it('always returns acquired and warns once when BroadcastChannel is undefined', async () => {
    const realBC = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    try {
      delete (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
      const warnings: unknown[][] = []
      const warn = (...args: unknown[]) => {
        warnings.push(args)
      }

      const mgr = createBroadcastChannelLockManager({
        channelName: 'unused',
        acquireTimeoutMs: 10,
        logger: { warn },
      })

      const r1 = await mgr.acquire(treeId('t-1'))
      const r2 = await mgr.acquire(treeId('t-2'))
      expect(r1.acquired).toBe(true)
      expect(r2.acquired).toBe(true)
      // Warn only once across multiple acquires — quiet for the operator.
      expect(warnings).toHaveLength(1)

      // release is a no-op in the degraded mode; covered for completeness.
      expect(() => mgr.release(treeId('t-1'))).not.toThrow()

      mgr.close()
    } finally {
      ;(globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel = realBC
    }
  })

  it('close is a no-op when no channel was constructed', () => {
    const realBC = (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
    try {
      delete (globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel
      const mgr = createBroadcastChannelLockManager({
        channelName: 'unused',
        logger: { warn: () => undefined },
      })
      expect(() => mgr.close()).not.toThrow()
    } finally {
      ;(globalThis as { BroadcastChannel?: typeof BroadcastChannel }).BroadcastChannel = realBC
    }
  })
})

// ============================================================================
// 5. close() — listener teardown
// ============================================================================

describe('createBroadcastChannelLockManager — close', () => {
  it('after close, the closed manager no longer responds as holder for new requests', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-A',
      acquireTimeoutMs: 20,
    })
    const b = createBroadcastChannelLockManager({
      channelName: name,
      tabId: 'tab-B',
      acquireTimeoutMs: 20,
    })
    try {
      await a.acquire(treeId('t-1'))
      // A holds the lock. Close A. B should now be able to acquire because
      // the holder-response handler is gone.
      a.close()
      await settle()

      const rb = await b.acquire(treeId('t-1'))
      expect(rb.acquired).toBe(true)
    } finally {
      b.close()
    }
  })

  it('acquire after close throws (fail-loud over silent-lie per rubber-duck Finding J)', async () => {
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      acquireTimeoutMs: 10,
    })
    mgr.close()
    await expect(mgr.acquire(treeId('t-1'))).rejects.toThrow(/closed/i)
  })

  it('release after close is a no-op (NOT throw — release is best-effort)', () => {
    // Release is the cleanup path; throwing here would surface inside the
    // shim's outer finally and cascade into "the wave settled but the lock
    // also blew up." Best-effort idempotency is the right semantic.
    const mgr = createBroadcastChannelLockManager({
      channelName: nextChannelName(),
      acquireTimeoutMs: 10,
    })
    mgr.close()
    expect(() => mgr.release(treeId('t-1'))).not.toThrow()
  })
})

// ============================================================================
// 6. tabId — auto-mint when not provided
// ============================================================================

describe('createBroadcastChannelLockManager — tabId', () => {
  it('mints a unique tabId when not provided', async () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({ channelName: name, acquireTimeoutMs: 20 })
    const b = createBroadcastChannelLockManager({ channelName: name, acquireTimeoutMs: 20 })
    try {
      await a.acquire(treeId('t-1'))
      const rb = await b.acquire(treeId('t-1'))
      expect(rb.acquired).toBe(false)
      if (!rb.acquired) {
        expect(rb.holderTabId).toMatch(/.+/) // non-empty string
        expect(rb.holderTabId).not.toBe('')
      }
    } finally {
      a.close()
      b.close()
    }
  })

  it('different managers mint different tabIds', () => {
    const name = nextChannelName()
    const a = createBroadcastChannelLockManager({ channelName: name })
    const b = createBroadcastChannelLockManager({ channelName: name })
    try {
      expect(a.tabId).not.toBe(b.tabId)
    } finally {
      a.close()
      b.close()
    }
  })
})
