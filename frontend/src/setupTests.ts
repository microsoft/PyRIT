import "@testing-library/jest-dom";

// jsdom does not implement BroadcastChannel; install a minimal in-process
// polyfill that matches the native semantics exactly (postMessage delivers
// a real MessageEvent to every other instance constructed with the same
// channel name in this process). Production uses the browser's native
// BroadcastChannel; this shim only exists for jest-jsdom.
//
// Rationale (PR4e+f.1 review): the `broadcast-channel` npm polyfill was
// considered but rejected because (a) its `onmessage(rawData)` calling
// convention differs from native `onmessage(MessageEvent)`, forcing a
// normalization shim in the runner code; (b) its simulate mode bypasses
// structured-clone serialization, so any non-JSON field added to a wire
// message would pass tests but fail in production; (c) it adds 7
// transitive dependencies for a 25-line problem.
type MessageCallback = (event: MessageEvent) => void
class InProcessBroadcastChannel {
  private static byName = new Map<string, Set<InProcessBroadcastChannel>>()
  private listeners = new Set<MessageCallback>()
  private _onmessage: MessageCallback | null = null
  public readonly name: string
  private closed = false

  constructor(name: string) {
    this.name = name
    let set = InProcessBroadcastChannel.byName.get(name)
    if (!set) {
      set = new Set()
      InProcessBroadcastChannel.byName.set(name, set)
    }
    set.add(this)
  }

  postMessage(data: unknown): void {
    if (this.closed) throw new Error("BroadcastChannel is closed")
    const peers = InProcessBroadcastChannel.byName.get(this.name)
    if (!peers) return
    // Async delivery via microtask to match native sync-emit-but-async-receive
    // semantics; tests await two microtask hops to settle a round trip.
    queueMicrotask(() => {
      for (const peer of peers) {
        if (peer === this) continue
        if (peer.closed) continue
        const event = new MessageEvent("message", { data })
        if (peer._onmessage) peer._onmessage(event)
        for (const l of peer.listeners) l(event)
      }
    })
  }

  set onmessage(fn: MessageCallback | null) {
    this._onmessage = fn
  }
  get onmessage(): MessageCallback | null {
    return this._onmessage
  }

  addEventListener(_type: "message", fn: MessageCallback): void {
    this.listeners.add(fn)
  }
  removeEventListener(_type: "message", fn: MessageCallback): void {
    this.listeners.delete(fn)
  }

  close(): void {
    if (this.closed) return
    this.closed = true
    this.listeners.clear()
    this._onmessage = null
    InProcessBroadcastChannel.byName.get(this.name)?.delete(this)
  }
}
;(globalThis as unknown as { BroadcastChannel: typeof InProcessBroadcastChannel }).BroadcastChannel =
  InProcessBroadcastChannel

// Set Vite-equivalent env vars for tests (the AST transformer rewrites
// import.meta.env.X → process.env.X, so these must exist as process.env).
process.env.VITE_API_URL = "http://localhost:8000/api";
process.env.MODE = "test";
process.env.DEV = "true";
process.env.PROD = "false";

// Mock window.matchMedia for Fluent UI components
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: jest.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
});

// Mock ResizeObserver for Fluent UI components
global.ResizeObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock IntersectionObserver
global.IntersectionObserver = jest.fn().mockImplementation(() => ({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
}));

// Mock scrollTo and scrollIntoView
Element.prototype.scrollTo = jest.fn();
Element.prototype.scrollIntoView = jest.fn();

// Mock URL.createObjectURL and URL.revokeObjectURL for file handling
global.URL.createObjectURL = jest.fn(() => "blob:mock-url");
global.URL.revokeObjectURL = jest.fn();
