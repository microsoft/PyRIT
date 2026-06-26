---
name: frontend-testing
description: PyRIT frontend test guidelines for React/TypeScript tests (frontend/**/*.test.{ts,tsx}). Use when creating, modifying, or reviewing frontend tests.
---

> Applies to `frontend/**/*.test.{ts,tsx}`.

# PyRIT Frontend Test Instructions

Jest + React Testing Library (RTL) + Fluent UI v9. This covers **PyRIT-specific** conventions. For generic RTL / jest-dom API details — query-priority ordering, `get`/`query`/`find` variants, the full `userEvent` API, `TextMatch` patterns, debugging helpers like `screen.debug()` / `logRoles` / `within` — follow the [Testing Library docs](https://testing-library.com/docs/) rather than restating them here.

## Test stack

| Tool | Purpose |
|---|---|
| **Jest** (`ts-jest`, `jsdom`) | Runner and assertions |
| **React Testing Library** | Component rendering and DOM queries |
| **`@testing-library/user-event`** | Realistic user interactions |
| **`@testing-library/jest-dom`** | DOM matchers (`toBeInTheDocument`, `toBeDisabled`, …) |
| **Playwright** | E2E (separate from unit tests) |

## File naming & location

Co-locate tests next to the source they test: `<ComponentName>.test.tsx`, or `<moduleName>.test.ts` for pure utilities (no JSX needed).

## Rendering Fluent UI components

Fluent UI v9 requires `FluentProvider` in the tree. Define a `TestWrapper` at the top of any file that renders Fluent components:

```tsx
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

render(<TestWrapper><MyComponent {...props} /></TestWrapper>)
```

For components that consume React Context (e.g. `useConnectionHealth`), either wrap with the real provider or mock the hook:

```tsx
jest.mock('@/hooks/useConnectionHealth', () => ({
  useConnectionHealth: () => ({ status: 'connected', reconnectCount: 0 }),
}))
```

## Query & interaction conventions

> "The more your tests resemble the way your software is used, the more confidence they can give you." — Kent C. Dodds. If a test breaks only because of a refactor (not a behavior change), it's too tightly coupled.

- **Query through `screen`** — don't destructure queries from `render()`.
- **Prefer semantic queries** (`getByRole('button', { name: /send/i })`, `getByLabelText`, `getByText`) over `container.querySelector` or CSS classes. Use `getByTestId` only when no semantic query works.
- **Use `userEvent` (with `userEvent.setup()`), not `fireEvent`** — it simulates the full browser event chain and enforces visibility/interactability. Create the `user` before `render()`, not inside `beforeEach`.
- **Async:** `findBy*` for appearance, `waitForElementToBeRemoved` for disappearance, `queryBy*` for absence assertions.
- Reset mocks with `beforeEach(() => jest.clearAllMocks())`.

```tsx
it('calls onSend when the user types and clicks send', async () => {
  const user = userEvent.setup()
  const onSend = jest.fn()
  render(<TestWrapper><ChatInputArea onSend={onSend} disabled={false} /></TestWrapper>)
  await user.type(screen.getByRole('textbox'), 'Hello')
  await user.click(screen.getByRole('button', { name: /send/i }))
  expect(onSend).toHaveBeenCalled()
})
```

## Mocking

**API:** mock the service objects from `src/services/api.ts` — do NOT mock Axios directly.

```tsx
jest.mock('@/services/api', () => ({
  attacksApi: { getMessages: jest.fn(), createAttack: jest.fn() },
}))
```

**Timers:** for polling/intervals, use `jest.useFakeTimers()` / `jest.useRealTimers()` and drive with `jest.advanceTimersByTime(...)`.

**Already mocked globally in `src/setupTests.ts` — do NOT re-mock:** `window.matchMedia`, `ResizeObserver`, `IntersectionObserver`, `Element.prototype.scrollTo` / `scrollIntoView`, `URL.createObjectURL` / `revokeObjectURL`, and `import.meta.env` (`VITE_API_URL`, `MODE`).

## What to test

- **Components:** rendering for given props (incl. empty / loading / error states), user interactions invoke the right callbacks, conditional rendering, accessibility (roles reachable, disabled states reflected).
- **Hooks:** state transitions and side effects (API calls fire, intervals set up and cleaned up). Prefer testing through a consuming component; use `renderHook` only for reusable library-style hooks.
- **Utils / services:** pure input → output for normal, boundary, and error inputs (`.test.ts`, no DOM).

Do **not** test Fluent UI internals, CSS / visual details, or internal state variable names.

## Coverage

Thresholds enforced in `jest.config.ts`: branches 85%, functions 90%, lines 90%, statements 85%. Run `cd frontend && npm run test:coverage`. Excluded: `main.tsx`, `vite-env.d.ts`, `services/api.ts` (thin Axios wrapper, tested indirectly).

## E2E (Playwright)

E2E tests live in `frontend/e2e/`, exercise full flows against a running backend, and do **not** mock APIs. Run `npm run test:e2e` (headless) or `npm run test:e2e:headed`.

## Common anti-patterns

| Anti-pattern | Do this instead |
|---|---|
| `container.querySelector('.my-class')` | `getByRole` / `getByText` / `getByTestId` |
| `expect(component.state.x).toBe(...)` | Assert what the user sees |
| `fireEvent.click(button)` for a user click | `await user.click(button)` |
| `getByText('Submit')` (no regex) | `getByRole('button', { name: /submit/i })` |
| `await waitFor(() => getByText('done'))` | `await findByText('done')` |
| Wrapping `render` in `act(...)` manually | `render(...)` already wraps in `act` |
| Tests that depend on execution order | Make each test independently runnable |
