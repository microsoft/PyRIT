---
applyTo: 'frontend/**/*.test.{ts,tsx}'
---

# PyRIT Frontend Test Instructions

Consistent, fast, readable tests using Jest + React Testing Library + Fluent UI v9.

## Test Stack

| Tool | Purpose |
|---|---|
| **Jest** (`ts-jest`, `jsdom`) | Test runner and assertions |
| **React Testing Library** (`@testing-library/react`) | Component rendering and DOM queries |
| **`@testing-library/user-event`** | Simulating realistic user interactions |
| **`@testing-library/jest-dom`** | Extended DOM matchers (`toBeInTheDocument`, `toBeDisabled`, etc.) |
| **Playwright** (`@playwright/test`) | E2E browser tests (separate from unit tests) |

## File Naming & Location

- Test files are **co-located** next to the source file they test.
- Naming: `<ComponentName>.test.tsx` or `<moduleName>.test.ts`

```
components/Chat/
  ChatWindow.tsx
  ChatWindow.styles.ts
  ChatWindow.test.tsx      ← co-located test
hooks/
  useConnectionHealth.tsx
  useConnectionHealth.test.tsx
utils/
  messageMapper.ts
  messageMapper.test.ts    ← for pure utility tests, use .test.ts (no JSX needed)
```

## Test Structure

### Describe / It Blocks
- Group tests with `describe('<ComponentName>', () => { ... })`.
- Use concise `it('should ...')` descriptions that state the expected behavior.

```tsx
describe('ConnectionBanner', () => {
  it('renders warning banner when degraded', () => { ... })
  it('renders error banner when disconnected', () => { ... })
})
```

### Setup & Teardown
- Use `beforeEach(() => jest.clearAllMocks())` to reset mocks between tests.
- Define `defaultProps` at the top of the `describe` block to avoid repetition.

```tsx
describe('ChatInputArea', () => {
  const defaultProps = {
    onSend: jest.fn(),
    disabled: false,
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render input and send button', () => {
    render(<TestWrapper><ChatInputArea {...defaultProps} /></TestWrapper>)
    expect(screen.getByRole('textbox')).toBeInTheDocument()
  })
})
```

## Rendering Components

### Fluent UI Provider Wrapper
Fluent UI v9 components require `FluentProvider` to be in the tree. Define a `TestWrapper` at the top of each test file for any test that renders Fluent UI components.

```tsx
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

const TestWrapper: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
)

// Usage
render(<TestWrapper><MyComponent {...props} /></TestWrapper>)
```

### Context Providers
For components that consume React Context (e.g., `useConnectionHealth`), wrap with the appropriate provider or mock the hook.

```tsx
// Option A: Mock the hook
jest.mock('@/hooks/useConnectionHealth', () => ({
  useConnectionHealth: () => ({ status: 'connected', reconnectCount: 0 }),
}))

// Option B: Wrap with the real provider
render(
  <ConnectionHealthProvider>
    <ComponentUnderTest />
  </ConnectionHealthProvider>
)
```

## Guiding Principle

> "The more your tests resemble the way your software is used, the more confidence they can give you." — Kent C. Dodds

Tests should interact with DOM nodes the way a real user would — not with component instances, internal state, or CSS class names. If a test breaks only because of a refactor (not a behavior change), the test is too tightly coupled.

## Querying the DOM

### Use `screen` for All Queries
Always import `screen` from `@testing-library/react` and query through it. Do not destructure queries from `render()` — `screen` is pre-bound to `document.body` and reads more clearly.

```tsx
// CORRECT
import { render, screen } from '@testing-library/react'
render(<MyComponent />)
screen.getByRole('button', { name: /submit/i })

// INCORRECT — destructuring from render
const { getByRole } = render(<MyComponent />)
getByRole('button', { name: /submit/i })
```

### Query Priority (follow Testing Library best practices)
Queries are ranked by how closely they mirror user experience. Prefer higher-priority queries:

**1. Accessible to everyone** — reflect how visual users and assistive technology find elements:

| Priority | Query | When to use |
|---|---|---|
| 1st | `getByRole` | Almost everything — buttons, textboxes, headings, dialogs, tabs. Use the `name` option to narrow: `getByRole('button', { name: /send/i })` |
| 2nd | `getByLabelText` | Form fields — emulates finding an input by its `<label>` |
| 3rd | `getByPlaceholderText` | Only when no label exists (a placeholder is NOT a substitute for a label) |
| 4th | `getByText` | Non-interactive elements (paragraphs, spans, banners) |
| 5th | `getByDisplayValue` | Filled-in form fields (current value of `<input>`, `<select>`) |

**2. Semantic queries** — HTML5 / ARIA attributes (less reliable across assistive tech):

| Priority | Query | When to use |
|---|---|---|
| 6th | `getByAltText` | `<img>`, `<area>`, `<input>` with `alt` text |
| 7th | `getByTitle` | `title` attribute — not consistently read by screen readers |

**3. Test IDs** — invisible to users, last resort:

| Priority | Query | When to use |
|---|---|---|
| 8th | `getByTestId` | When no semantic query works, or text is dynamic/unstable |

```tsx
// CORRECT — queries what the user sees
screen.getByRole('button', { name: /send/i })
screen.getByLabelText('Username')
screen.getByText(/unable to reach/i)

// ACCEPTABLE — for non-semantic containers
screen.getByTestId('connection-banner')

// INCORRECT — fragile, tied to implementation
container.querySelector('.fluentui-button-primary')
```

### `getByRole` Tips
- Use the `name` option (accessible name) to distinguish multiple elements with the same role: `getByRole('button', { name: /delete/i })`
- Filter by state: `{ checked: true }`, `{ selected: true }`, `{ pressed: true }`, `{ expanded: false }`, `{ busy: true }`, `{ current: 'page' }`
- Query headings by level: `getByRole('heading', { level: 2 })`
- If `getByRole` is slow on a large DOM, consider `getByLabelText` or `getByText` as a faster alternative

### Query Variants — `get` vs `query` vs `find`

| Variant | No match | 1 match | >1 match | Async? | Use when... |
|---|---|---|---|---|---|
| `getBy` | **throws** | returns element | **throws** | No | Element MUST be present right now |
| `queryBy` | returns `null` | returns element | **throws** | No | Asserting element is NOT present |
| `findBy` | **throws** (after timeout) | returns element | **throws** | Yes | Element will appear after async operation |
| `getAllBy` | **throws** | returns array | returns array | No | Multiple elements expected |
| `queryAllBy` | returns `[]` | returns array | returns array | No | Asserting count or absence of multiple |
| `findAllBy` | **throws** (after timeout) | returns array | returns array | Yes | Multiple elements appear asynchronously |

### Async Queries & Waiting

**Waiting for appearance** — use `findBy*` (preferred) or `waitFor`:

```tsx
// PREFERRED — findBy is a combination of getBy + waitFor
const successMsg = await screen.findByText(/attack complete/i)
expect(successMsg).toBeInTheDocument()

// ALTERNATIVE — waitFor for non-DOM assertions
await waitFor(() => {
  expect(mockApi).toHaveBeenCalledTimes(1)
})
```

**Asserting disappearance** — use `waitForElementToBeRemoved` or `waitFor` + `queryBy`:

```tsx
// PREFERRED — efficient, uses MutationObserver internally
await waitForElementToBeRemoved(() => screen.queryByText(/loading/i))

// ALTERNATIVE
await waitFor(() => {
  expect(screen.queryByText(/loading/i)).not.toBeInTheDocument()
})
```

**Asserting absence (synchronous)** — use `queryBy`:

```tsx
// CORRECT — returns null instead of throwing
expect(screen.queryByText(/error/i)).not.toBeInTheDocument()
expect(screen.queryAllByRole('alert')).toHaveLength(0)

// INCORRECT — getBy throws if missing, making the error message confusing
expect(screen.getByText(/error/i)).not.toBeInTheDocument() // throws before assertion
```

### TextMatch Patterns
Queries accept strings, regular expressions, or functions:

```tsx
// Exact string (default)
screen.getByText('Hello World')

// Regex — preferred for partial or case-insensitive matches
screen.getByText(/hello world/i)     // case-insensitive full match
screen.getByText(/hello/i)           // substring match
screen.getByRole('button', { name: /submit/i })

// Custom function (rare — for complex matching)
screen.getByText((content, element) => {
  return element?.tagName === 'SPAN' && content.startsWith('Error')
})
```

Prefer regex over `{ exact: false }` — regex gives you more control.

## User Interactions

### Use `userEvent` over `fireEvent`
`fireEvent` dispatches a single DOM event. `userEvent` simulates a full interaction — focus, keyboard events, input events, selection changes, blur — just like a real browser. It also enforces visibility and interactability checks (e.g., it won't click a hidden element or type in a disabled input).

Use `fireEvent` only for edge cases that `userEvent` doesn't support yet (e.g., custom drag events).

### Always call `userEvent.setup()` before rendering
Create the `user` instance before `render()`, not inside `beforeEach`. This ensures a clean event state per test.

```tsx
import userEvent from '@testing-library/user-event'

it('should call onSend when user types and clicks send', async () => {
  const user = userEvent.setup()
  const onSend = jest.fn()

  render(<TestWrapper><ChatInputArea onSend={onSend} disabled={false} /></TestWrapper>)

  await user.type(screen.getByRole('textbox'), 'Hello')
  await user.click(screen.getByRole('button', { name: /send/i }))

  expect(onSend).toHaveBeenCalled()
})
```

### Common `userEvent` APIs

| Method | Simulates |
|---|---|
| `user.click(element)` | Full click (pointerdown → mousedown → focus → pointerup → mouseup → click) |
| `user.dblClick(element)` | Double click |
| `user.type(element, text)` | Typing character-by-character into a focused element |
| `user.clear(element)` | Selecting all text and deleting it |
| `user.selectOptions(select, values)` | Selecting option(s) in a `<select>` |
| `user.upload(input, file)` | Uploading file(s) to a file input |
| `user.tab()` | Pressing Tab to move focus |
| `user.keyboard('{Enter}')` | Pressing specific keys |
| `user.hover(element)` / `user.unhover(element)` | Mouse enter / leave |
| `user.paste(text)` | Pasting from clipboard |

## Mocking

### API Mocks
Mock the API service objects from `src/services/api.ts`. Do NOT mock Axios directly in component tests.

```tsx
import { attacksApi } from '@/services/api'

jest.mock('@/services/api', () => ({
  attacksApi: {
    getMessages: jest.fn(),
    createAttack: jest.fn(),
  },
}))

const mockGetMessages = attacksApi.getMessages as jest.Mock
mockGetMessages.mockResolvedValue({ data: [...] })
```

### Timer Mocks
For components with `setInterval` / `setTimeout` (e.g., polling), use Jest fake timers.

```tsx
beforeEach(() => {
  jest.useFakeTimers()
})

afterEach(() => {
  jest.useRealTimers()
})

it('polls health endpoint every 60 seconds', () => {
  render(<ConnectionHealthProvider>...</ConnectionHealthProvider>)
  jest.advanceTimersByTime(60_000)
  expect(healthApi.checkHealth).toHaveBeenCalledTimes(1)
})
```

### Global Mocks
The following are already mocked globally in `src/setupTests.ts` — do NOT re-mock them:
- `window.matchMedia`
- `ResizeObserver`
- `IntersectionObserver`
- `Element.prototype.scrollTo` / `scrollIntoView`
- `URL.createObjectURL` / `revokeObjectURL`
- `import.meta.env` variables (`VITE_API_URL`, `MODE`)

## What to Test

### Components
- **Rendering**: correct output for given props (including edge cases like empty arrays, loading states, error states).
- **User interactions**: clicks, typing, form submissions invoke the correct callbacks.
- **Conditional rendering**: elements appear/disappear based on props or state.
- **Accessibility**: interactive elements are reachable by role; disabled states are reflected.

### Hooks
- **State transitions**: initial state, state after actions, error states.
- **Side effects**: API calls are triggered, intervals are set up and cleaned up.
- Prefer testing hooks through a consuming component. Use `renderHook` only for reusable library-style hooks that are difficult to test through a component.

```tsx
import { renderHook, act } from '@testing-library/react'

it('returns connected status initially', () => {
  const { result } = renderHook(() => useConnectionHealth(), {
    wrapper: ConnectionHealthProvider,
  })
  expect(result.current.status).toBe('connected')
})
```

Note: `result.current` always holds the latest committed value (like a ref). Access it after `act()` to see state changes.

### Utils / Services
- **Pure functions**: input → output for normal cases, boundary values, and error inputs.
- No DOM rendering needed — use plain `.test.ts` files.

### What NOT to Test
- Fluent UI component internals (trust the library).
- CSS styling details (test behavior and accessibility, not visual appearance).
- Implementation details like internal state variable names.

## Debugging Tests

### `screen.debug()`
Use `screen.debug()` to print the current DOM to the console when a test is failing. This is far more useful than inspecting the raw container.

```tsx
screen.debug()                         // prints entire document.body
screen.debug(screen.getByRole('dialog'))  // prints a specific subtree
screen.debug(undefined, 30000)         // increase max output length
```

### `logRoles`
When you're not sure which roles are available, use `logRoles` to see every element's implicit and explicit role:

```tsx
import { logRoles } from '@testing-library/react'

const { container } = render(<MyComponent />)
logRoles(container)
```

## Scoped Queries with `within`

Use `within` to scope queries to a specific subtree — useful when the same text or role appears multiple times on the page.

```tsx
import { within } from '@testing-library/react'

const dialog = screen.getByRole('dialog')
const confirmButton = within(dialog).getByRole('button', { name: /confirm/i })
```

## Assertions

### Prefer `@testing-library/jest-dom` Matchers

```tsx
// CORRECT — expressive, readable
expect(button).toBeDisabled()
expect(banner).toBeInTheDocument()
expect(input).toHaveValue('hello')

// INCORRECT — testing implementation details
expect(button.disabled).toBe(true)
expect(document.querySelector('[data-testid="banner"]')).not.toBeNull()
```

### Callback Assertions

```tsx
// Verify a callback was called
expect(onSend).toHaveBeenCalledTimes(1)

// Inspect call arguments when assertion with `toHaveBeenCalledWith` is awkward
const [firstArg] = onSend.mock.calls[0]
expect(firstArg.content).toBe('Hello')
```

## Coverage

Coverage thresholds are enforced globally in `jest.config.ts`:

| Metric | Threshold |
|---|---|
| Branches | 85% |
| Functions | 90% |
| Lines | 90% |
| Statements | 85% |

Run coverage locally:
```bash
cd frontend && npm run test:coverage
```

Files excluded from coverage: `main.tsx`, `vite-env.d.ts`, `services/api.ts` (thin Axios wrapper, tested indirectly).

## E2E Tests (Playwright)

- E2E tests live in `frontend/e2e/`.
- They test full user flows against a running backend.
- Do NOT mock APIs in E2E tests — they exercise the real stack.
- Run with `npm run test:e2e` (headless) or `npm run test:e2e:headed`.

## Common Anti-Patterns

| Anti-pattern | Why it's wrong | Do this instead |
|---|---|---|
| `container.querySelector('.my-class')` | Couples test to CSS class names | Use `getByRole`, `getByText`, or `getByTestId` |
| `expect(component.state.count).toBe(1)` | Tests internal state, not behavior | Assert on what the user sees: `expect(screen.getByText('1')).toBeInTheDocument()` |
| `fireEvent.click(button)` for a user click | Doesn't simulate the full browser event chain | Use `await user.click(button)` |
| `getByText('Submit')` without regex | Brittle — breaks on case changes or whitespace | Use `getByRole('button', { name: /submit/i })` |
| `await waitFor(() => getByText('done'))` | `getBy` already throws — redundant wrapping | Use `await findByText('done')` |
| Wrapping `render` in `act(...)` manually | RTL's `render` already wraps in `act` | Just call `render(<Component />)` |
| Tests that depend on execution order | Fragile, hides bugs | Each test must be independently runnable |

## Final Checklist

Before committing frontend tests, ensure:
- [ ] Tests are co-located next to the source file
- [ ] Fluent UI components are wrapped in `FluentProvider` via `TestWrapper`
- [ ] DOM queries follow priority: `getByRole` > `getByLabelText` > `getByText` > `getByTestId`
- [ ] `screen` is used for all queries (not destructured from `render`)
- [ ] User interactions use `userEvent.setup()`, not `fireEvent`
- [ ] `queryBy*` used for absence assertions, `findBy*` for async appearance
- [ ] API mocks target service objects, not Axios internals
- [ ] `jest.clearAllMocks()` in `beforeEach`
- [ ] No testing of Fluent UI internals, CSS details, or internal state
- [ ] Async operations use `findBy*` queries, `waitFor`, or `waitForElementToBeRemoved`
- [ ] Coverage thresholds still pass (`npm run test:coverage`)
