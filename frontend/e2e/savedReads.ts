import type { Request, Response } from '@playwright/test'

import type { AttackMetadata } from '@/types'

type SavedRequest = Pick<Request, 'method' | 'url'>
type MetadataOutcome = { readonly ok: true; readonly body: AttackMetadata }
  | { readonly ok: false; readonly error: unknown }

interface SavedMetadataRead {
  readonly url: URL
  status?: number
  readonly outcome: Promise<MetadataOutcome>
}

interface PendingMetadataRead {
  readonly read: SavedMetadataRead
  readonly resolve: (outcome: MetadataOutcome) => void
}

interface SavedReadEvents {
  on(event: 'request' | 'requestfailed', listener: (request: Request) => void): unknown
  on(event: 'response', listener: (response: Response) => void): unknown
  off(event: 'request' | 'requestfailed', listener: (request: Request) => void): unknown
  off(event: 'response', listener: (response: Response) => void): unknown
}

class MetadataReadError extends Error {
  readonly cause: unknown

  constructor(failures: Array<{ readonly url: URL; readonly error: unknown }>) {
    super(failures.map(({ url, error }): string => `Cannot read saved metadata at ${url}: ${String(error)}`).join('\n'))
    this.cause = failures[0].error
  }
}

/** Owns metadata requests from request start through body completion. */
export class SavedReads {
  readonly requests: URL[] = []
  readonly metadata: SavedMetadataRead[] = []
  private readonly pending = new Map<SavedRequest, PendingMetadataRead>()
  private phaseStart = 0
  private changed = Promise.withResolvers<void>()

  request(request: SavedRequest): void {
    const url = new URL(request.url())
    if (request.method() !== 'GET' || !/\/api\/attacks\/[^/]+(?:\/(?:messages|conversations|metadata))?$/.test(url.pathname)) return
    this.requests.push(url)
    if (!url.pathname.endsWith('/metadata')) return
    const completion = Promise.withResolvers<MetadataOutcome>()
    const read: SavedMetadataRead = { url, outcome: completion.promise }
    this.metadata.push(read)
    this.pending.set(request, { read, resolve: completion.resolve })
    this.changed.resolve()
    this.changed = Promise.withResolvers<void>()
  }

  response(request: SavedRequest, status: number, readBody: () => Promise<AttackMetadata>): void {
    const pending = this.pending.get(request)
    if (!pending) return
    pending.read.status = status
    // Both branches fulfill: a body failure is retained until an awaited checkpoint reports it.
    void readBody().then(
      (metadata: AttackMetadata): void => { this.complete(request, { ok: true, body: metadata }) },
      (error: unknown): void => { this.complete(request, { ok: false, error }) },
    )
  }

  failed(request: SavedRequest, error: unknown): void {
    // After headers, the owned body promise must settle before cleanup can finish.
    if (this.pending.get(request)?.read.status === undefined) this.complete(request, { ok: false, error })
  }

  get pendingCount(): number {
    return this.pending.size
  }

  private complete(request: SavedRequest, outcome: MetadataOutcome): void {
    const pending = this.pending.get(request)
    if (!pending) return
    pending.resolve(outcome)
    this.pending.delete(request)
  }

  async drain(): Promise<void> {
    let count: number
    let outcomes: MetadataOutcome[]
    do {
      count = this.metadata.length
      outcomes = await Promise.all(this.metadata.map((read: SavedMetadataRead): Promise<MetadataOutcome> => read.outcome))
    } while (count !== this.metadata.length)
    const failures = outcomes.flatMap((outcome: MetadataOutcome, index: number) =>
      outcome.ok ? [] : [{ url: this.metadata[index].url, error: outcome.error }])
    if (failures.length) throw new MetadataReadError(failures)
  }

  async beforeNavigation(
    attackId: string, conversationId: string, navigate: () => Promise<unknown>,
  ): Promise<void> {
    // A request owns its completion before headers arrive; an empty body queue is not readiness.
    while (!this.metadata.slice(this.phaseStart).some((read: SavedMetadataRead): boolean =>
      read.url.pathname === `/api/attacks/${attackId}/metadata`
      && read.url.searchParams.get('conversation_id') === conversationId)) {
      await this.changed.promise
    }
    await this.drain()
    this.phaseStart = this.metadata.length
    await navigate()
  }
}

export function captureSavedReads(page: SavedReadEvents): { readonly reads: SavedReads; readonly dispose: () => Promise<void> } {
  const reads = new SavedReads()
  const request = (value: Request): void => { reads.request(value) }
  const response = (value: Response): void => {
    const url = new URL(value.url())
    if (value.request().method() === 'GET' && /\/api\/attacks\/[^/]+\/metadata$/.test(url.pathname)) {
      reads.response(value.request(), value.status(), () => value.json())
    }
  }
  const failed = (value: Request): void => {
    reads.failed(value, new Error(value.failure()?.errorText ?? `Request failed: ${value.url()}`))
  }
  page.on('request', request)
  page.on('response', response)
  page.on('requestfailed', failed)
  return {
    reads,
    dispose: async (): Promise<void> => {
      page.off('request', request)
      try {
        await reads.drain()
      } finally {
        page.off('response', response)
        page.off('requestfailed', failed)
      }
    },
  }
}
