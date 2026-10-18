import { isCompatibilityId } from '@/utils/compatibilityId'

declare const __PYRIT_COMPATIBILITY_ID__: string

export const COMPATIBILITY_HEADER = 'PyRIT-Compatibility-ID'

export type CompatibilitySnapshot =
  | { status: 'checking' | 'ready' }
  | { status: 'blocked'; reason: string; expected?: string; actual?: string }

export class CompatibilityStore {
  private snapshot: CompatibilitySnapshot = { status: 'checking' }
  private listeners = new Set<() => void>()
  private handshake: Promise<void> | undefined

  constructor(readonly bundledId: string) {}

  getSnapshot = (): CompatibilitySnapshot => this.snapshot

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }

  private publish(snapshot: CompatibilitySnapshot): void {
    this.snapshot = snapshot
    this.listeners.forEach((listener) => listener())
  }

  block(reason: string, expected?: unknown, actual?: unknown): void {
    if (this.snapshot.status === 'blocked') return
    this.publish({
      status: 'blocked',
      reason,
      expected: typeof expected === 'string' ? expected : undefined,
      actual: typeof actual === 'string' ? actual : undefined,
    })
  }

  assertReady(): void {
    if (this.snapshot.status !== 'ready') {
      throw new Error('PyRIT compatibility has not been verified or is blocked. Reload a matching frontend and backend.')
    }
  }

  verify(fetchVersion: () => Promise<unknown>): Promise<void> {
    if (this.handshake) return this.handshake
    this.handshake = this.runHandshake(fetchVersion)
    return this.handshake
  }

  private async runHandshake(fetchVersion: () => Promise<unknown>): Promise<void> {
    if (this.snapshot.status !== 'checking') return
    if (!isCompatibilityId(this.bundledId)) {
      this.block('This frontend has a missing or invalid build identity.')
      return
    }
    try {
      const version = await fetchVersion()
      const backendId = version && typeof version === 'object' && 'compatibility_id' in version
        ? version.compatibility_id : undefined
      if (!isCompatibilityId(backendId)) {
        this.block('The backend returned a missing or invalid compatibility identity.', backendId, this.bundledId)
      } else if (backendId !== this.bundledId) {
        this.block('The frontend and backend are different builds.', backendId, this.bundledId)
      } else if (this.snapshot.status === 'checking') {
        this.publish({ status: 'ready' })
      }
    } catch {
      this.block('The authenticated backend version check failed. Check your connection and sign-in before reloading.')
    }
  }
}

export const compatibility = new CompatibilityStore(
  typeof __PYRIT_COMPATIBILITY_ID__ === 'string' ? __PYRIT_COMPATIBILITY_ID__ : '',
)
