// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Cross-tab advisory lock backed by `BroadcastChannel('pyrit-runner')`.
 *
 * Per doc/gui/design/01 §9.4.3 and doc/gui/design/03 §10.4: two browser
 * tabs viewing the same `conversation_tree_id` can independently fire
 * `maxParallel=4` POSTs and blow the cap. The lock is advisory (not
 * transactional) — a holder tab posts a `lock_busy` reply when another
 * tab requests the same tree's lock; the requester waits a short window
 * (default 50ms) for any holder to chime in, then assumes the lock is
 * available.
 *
 * Wire format on the channel:
 *   { type: 'lock_request',  treeId, requestId, tabId }
 *   { type: 'lock_busy',     requestId, holderTabId }
 *   { type: 'lock_released', treeId }
 *
 * `MessagePort` transfer-list is NOT used (BroadcastChannel does not
 * accept transferable objects); request/reply correlation rides on the
 * `requestId` field per the rev-10 correctness note in §9.4.3.
 *
 * Browser compatibility: when `typeof BroadcastChannel === 'undefined'`
 * (Safari ≤15.3), this manager degrades to always-acquired and warns
 * once — the operator gets the V1.0 fork-bomb risk but the rest of the
 * runner keeps working. Tests in jsdom load the `broadcast-channel`
 * npm polyfill via `setupTests.ts` (simulate mode, in-process).
 */

import type { ConversationTreeId, CrossTabLockManager, LockAcquireResult } from './treeTypes'

interface BroadcastChannelLike {
  postMessage(message: unknown): unknown
  close(): void
  onmessage: ((event: MessageEvent) => void) | null
}

interface BroadcastChannelCtor {
  new (name: string): BroadcastChannelLike
}

interface Logger {
  warn(...args: unknown[]): void
}

export interface BroadcastChannelLockManagerOptions {
  /** Channel name; production passes 'pyrit-runner' per the spec. */
  channelName?: string
  /** Stable diagnostic id for this tab; auto-minted via the supplied uuid. */
  tabId?: string
  /** Acquire-window timeout in ms. Default 50 per 01 §9.4.3. */
  acquireTimeoutMs?: number
  /** Replaceable for tests + non-default logging. Default `console`. */
  logger?: Logger
  /** Replaceable so tests can inject deterministic ids. Default `crypto.randomUUID()`. */
  uuid?: () => string
}

export interface BroadcastChannelLockManager extends CrossTabLockManager {
  /** Stop responding to holder requests. Idempotent. */
  close(): void
  /** Exposed for tests + the busy-modal "this tab" hint. */
  readonly tabId: string
}

const DEFAULT_CHANNEL_NAME = 'pyrit-runner'
const DEFAULT_ACQUIRE_TIMEOUT_MS = 50

export function createBroadcastChannelLockManager(
  options: BroadcastChannelLockManagerOptions = {},
): BroadcastChannelLockManager {
  const uuid = options.uuid ?? defaultUuid
  const tabId = options.tabId ?? uuid()
  const logger = options.logger ?? console
  const acquireTimeoutMs = options.acquireTimeoutMs ?? DEFAULT_ACQUIRE_TIMEOUT_MS
  const channelName = options.channelName ?? DEFAULT_CHANNEL_NAME

  const ctor = (globalThis as { BroadcastChannel?: BroadcastChannelCtor }).BroadcastChannel
  if (ctor === undefined) {
    // Graceful degradation: warn once, then always-acquired. Operators on
    // older Safari accept the V1.0 fork-bomb risk; everything else keeps
    // working.
    logger.warn(
      'BroadcastChannel is not available in this environment; cross-tab lock disabled. ' +
        'Concurrent waves across tabs on the same tree may exceed the maxParallel cap.',
    )
    let degradedClosed = false
    return {
      tabId,
      acquire: async () => {
        if (degradedClosed) throw new Error('cross-tab lock manager is closed')
        return { acquired: true }
      },
      release: () => undefined,
      close: () => {
        degradedClosed = true
      },
    }
  }

  const channel = new ctor(channelName)
  const heldLocks = new Set<ConversationTreeId>()
  // Subscribers receive every incoming message; both the persistent
  // holder-response handler and the per-acquire busy listener register
  // here so we have one onmessage dispatcher.
  const subscribers = new Set<(data: WireMessage) => void>()
  channel.onmessage = (event) => {
    const data = event.data as WireMessage | undefined
    if (data === undefined || typeof data !== 'object') return
    for (const fn of subscribers) fn(data)
  }

  // Persistent holder-response handler: reply with `lock_busy` for any
  // request targeting a tree we hold.
  const onRequest = (data: WireMessage) => {
    if (data.type !== 'lock_request') return
    if (!heldLocks.has(data.treeId as ConversationTreeId)) return
    void channel.postMessage({
      type: 'lock_busy',
      requestId: data.requestId,
      holderTabId: tabId,
    } satisfies WireMessage)
  }
  subscribers.add(onRequest)

  let closed = false
  return {
    tabId,
    acquire: async (treeId) => {
      // Fail loudly on closed-manager use — silent "acquired" would be a
      // non-functional lock (no holder responses, no peer requests handled);
      // throwing makes the bug surface at the caller rather than turning
      // into a phantom cross-tab race.
      if (closed) throw new Error('cross-tab lock manager is closed')
      // Same-tab reacquire is a no-op: the §9.4.3 protocol explicitly
      // short-circuits because the request would race our own holder-
      // response handler.
      if (heldLocks.has(treeId)) return { acquired: true }

      const requestId = uuid()
      const result = await new Promise<LockAcquireResult>((resolve) => {
        const listener = (data: WireMessage) => {
          if (data.type !== 'lock_busy') return
          if (data.requestId !== requestId) return
          cleanup()
          resolve({ acquired: false, holderTabId: data.holderTabId })
        }
        const timer = setTimeout(() => {
          cleanup()
          // No other tab claimed the lock; it's ours.
          resolve({ acquired: true })
        }, acquireTimeoutMs)
        function cleanup() {
          subscribers.delete(listener)
          clearTimeout(timer)
        }
        subscribers.add(listener)
        void channel.postMessage({
          type: 'lock_request',
          treeId: treeId as string,
          requestId,
          tabId,
        } satisfies WireMessage)
      })

      if (result.acquired) heldLocks.add(treeId)
      return result
    },
    release: (treeId) => {
      if (closed) return
      if (!heldLocks.delete(treeId)) return
      void channel.postMessage({
        type: 'lock_released',
        treeId: treeId as string,
      } satisfies WireMessage)
    },
    close: () => {
      if (closed) return
      closed = true
      subscribers.clear()
      channel.onmessage = null
      try {
        channel.close()
      } catch {
        // Polyfill may throw if already closed elsewhere — safe to swallow.
      }
    },
  }
}

// ============================================================================
// Wire types
// ============================================================================

type WireMessage =
  | { type: 'lock_request'; treeId: string; requestId: string; tabId: string }
  | { type: 'lock_busy'; requestId: string; holderTabId: string }
  | { type: 'lock_released'; treeId: string }

function defaultUuid(): string {
  // crypto.randomUUID is available in all modern browsers and Node 19+.
  // Fallback to a simple random string for any environment without it
  // (e.g., very old browsers that we're not supporting beyond the
  // BroadcastChannel-undefined degradation).
  const cryptoGlobal = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto
  if (cryptoGlobal?.randomUUID) return cryptoGlobal.randomUUID()
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`
}
