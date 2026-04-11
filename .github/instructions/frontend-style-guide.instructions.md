---
applyTo: 'frontend/**/*.{ts,tsx}'
---

# PyRIT Frontend Style Guide — TypeScript, React & Fluent UI v9

Follow these standards to keep the frontend consistent, accessible, and maintainable.

## TypeScript

### Strict Mode & `@ts-ignore`
- The project enforces `"strict": true` in tsconfig.
- Do NOT use `@ts-ignore`. It silences the compiler error without fixing the underlying issue, making future type changes silently unsafe. If you absolutely must suppress a check, use `@ts-expect-error` with a comment explaining why.

### Naming Conventions

| Style | Used for |
|---|---|
| `UpperCamelCase` | Components, classes, interfaces, type aliases, enums |
| `lowerCamelCase` | Variables, parameters, functions, methods, properties, hooks |
| `CONSTANT_CASE` | Top-level or static `readonly` constants |

- **Descriptive names**: Names must be clear to a new reader. Do not use ambiguous abbreviations. Exception: loop variables in ≤10-line scopes may use short names.
- **Treat acronyms as words**: `loadHttpUrl`, not `loadHTTPURL`.
- **No `I` prefix on interfaces**: Use `UserProps`, not `IUserProps`.
- **No `_` prefix/suffix on identifiers**: Instead use TypeScript's `private` keyword for visibility. Unused parameters should use destructuring skips (`[a, , b]`) or an `_`-prefixed arg name only when required by a callback signature.

### Variables
- Always use `const` or `let`. Never use `var`.
- Default to `const`. Only use `let` when you need reassignment.

### Equality
- Always use `===` and `!==`. Never use `==` or `!=`.
- Exception: `== null` is allowed to check for both `null` and `undefined`.

### Type Declarations
- **Every** function parameter MUST have an explicit type annotation.
- Return types SHOULD be annotated — especially on exported functions and complex returns. They MAY be omitted when the return is trivially obvious.
- Leave out type annotations for trivially inferred initializers (`string`, `number`, `boolean`, `RegExp` literal, or `new` expression).

```tsx
// CORRECT — type is obvious from the initializer
const name = 'PyRIT'
const count = 0
const items = new Map<string, number>()

// CORRECT — non-obvious, annotate
const config: AppConfig = loadConfig()

// INCORRECT — redundant annotation
const name: string = 'PyRIT'
const count: number = 0
```

- Prefer `interface` for component props and object shapes. Use `type` for unions, intersections, and mapped types.
- Avoid `enum` — use `as const` objects or union literal types instead.

```tsx
// CORRECT
interface ChatMessageProps {
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  attachments?: MessageAttachment[]
}

// INCORRECT — enum
enum Role { User, Assistant }
```

### Array Type Syntax
- Use `T[]` for simple types (alphanumeric): `string[]`, `number[]`, `Message[]`.
- Use `Array<T>` for complex types: `Array<string | number>`, `Array<[string, number]>`.

```tsx
// CORRECT
const names: string[] = []
const pairs: Array<[string, number]> = []

// INCORRECT
const names: Array<string> = []       // use string[] for simple types
const pairs: [string, number][] = []  // hard to read, use Array<>
```

### Type Assertions
- Use `as` syntax, never angle-bracket syntax (`<Foo>x`).
- Prefer type annotations (`: Foo`) over type assertions (`as Foo`) on object literals — assertions bypass excess-property checking.
- Avoid non-nullability assertions (`!`). If you must use one, add a comment explaining why it's safe.
- Prefer `unknown` over `any`. Use `any` only when truly necessary, and add a comment justifying it.

```tsx
// CORRECT — annotation catches extra/missing properties
const user: User = { name: 'Alice', email: 'a@b.com' }

// INCORRECT — assertion silently ignores typos
const user = { name: 'Alice', emial: 'a@b.com' } as User

// CORRECT — safe narrowing
if (value instanceof Error) {
  console.error(value.message)
}

// LAST RESORT — with justification
// Response is guaranteed to have a body by the middleware
const body = response.body!
```

### `readonly`
- Mark properties, fields, and parameters that are never reassigned with `readonly`.

```tsx
interface Config {
  readonly apiUrl: string
  readonly timeout: number
}
```

### Centralized Types
- All shared TypeScript types live in `src/types/index.ts`.
- Component-local types (e.g., internal state shapes) may be defined in the component file.
- Do NOT define shared types in component files.
- Do NOT include `| null` or `| undefined` in type aliases. Add nullability at the point of use.

```tsx
// CORRECT
type CoffeeResponse = Latte | Americano
function getCoffee(): CoffeeResponse | undefined { ... }

// INCORRECT — bakes nullability into the alias
type CoffeeResponse = Latte | Americano | undefined
```

- Use optional fields (`?`) rather than `| undefined` when a property can be absent.

### Import Aliases
- Use the `@/` path alias for imports from `src/`. Do not use deeply nested relative paths.
- Use relative imports (`./foo`) for files within the same feature directory.

```tsx
// CORRECT
import { Message } from '@/types'
import { backendMessagesToFrontend } from '@/utils/messageMapper'
import { useChatWindowStyles } from './ChatWindow.styles' // same directory

// INCORRECT
import { Message } from '../../../types'
```

### Import Organization
Group imports in this order, separated by blank lines:

```tsx
// 1. React
import React, { useState, useCallback } from 'react'

// 2. Third-party libraries
import { Button, Text } from '@fluentui/react-components'
import { AddRegular } from '@fluentui/react-icons'
import axios from 'axios'

// 3. Internal — absolute (@/) imports
import { Message } from '@/types'

// 4. Local / relative imports
import { useChatWindowStyles } from './ChatWindow.styles'
import MessageList from './MessageList'
```

## React Components

### Function Components Only
- Always use function components. Never use class components.
- Prefer named function declarations over arrow-function assignments for top-level components.

```tsx
// CORRECT
export default function ChatWindow({ messages }: ChatWindowProps) { ... }

// ACCEPTABLE for non-default exports
export const ConnectionBanner = ({ status }: ConnectionBannerProps) => { ... }

// INCORRECT
class ChatWindow extends React.Component { ... }
```

### Props
- Define a dedicated `interface` for every component's props, named `<ComponentName>Props`.
- Destructure props in the function signature.
- Use `children: React.ReactNode` when accepting children.

```tsx
interface MessageListProps {
  messages: Message[]
  isLoading: boolean
}

export default function MessageList({ messages, isLoading }: MessageListProps) { ... }
```

### Hooks

#### Rules of Hooks
- **Call hooks at the top level only.** Never inside loops, conditions, nested functions, `try`/`catch`/`finally` blocks, or after early returns. This ensures hooks run in the same order every render.
- **Call hooks only from React function components or custom hooks.** Never from plain functions, class components, or event handlers.
- Never pass hooks around as regular values or call them dynamically. Do not write higher-order hooks ("hooks that return hooks").
- These rules are enforced by `eslint-plugin-react-hooks`.

```tsx
// CORRECT
function ChatWindow() {
  const [messages, setMessages] = useState<Message[]>([])
  const styles = useChatWindowStyles()
  // ...
}

// INCORRECT — conditional hook call
function ChatWindow({ isReady }: { isReady: boolean }) {
  if (isReady) {
    const [messages, setMessages] = useState<Message[]>([]) // ❌
  }
}
```

#### Memoization
- Wrap expensive callbacks with `useCallback` and expensive computations with `useMemo` — but only when there is a measurable benefit or the value is passed as a dependency.
- Do NOT wrap every value by default — premature memoization obscures code.

#### Custom Hooks
- Custom hooks go in `src/hooks/` and MUST start with `use`.
- Prefer extracting reusable Effects into custom hooks to reduce raw `useEffect` calls in components.

### Purity & Render Safety
Components and hooks must be **pure** during rendering:

- **Idempotent:** Given the same inputs (props, state, context), a component must return the same JSX. No reading from external mutable sources during render.
- **No side effects in render:** Do not modify variables, objects, or DOM outside the component's scope during rendering. Side effects belong in event handlers or `useEffect`.
- **Props and state are immutable snapshots.** Never mutate them directly. Always use setter functions (`setState`) and create new objects/arrays when updating.
- **Don't mutate values after passing them to JSX.** Move all mutations before the JSX return so React can render predictably.
- **Use components in JSX, don't call them as functions.** Write `<Article />`, not `Article()`. Let React manage the component lifecycle.

```tsx
// CORRECT — pure: same result every render for same inputs
function Greeting({ name }: { name: string }) {
  return <Text>Hello, {name}</Text>
}

// INCORRECT — side effect during render
function Counter() {
  let count = 0
  count++ // ❌ mutating local variable used in render
  return <Text>{count}</Text>
}

// CORRECT — create new object to update state
setMessages((prev) => [...prev, newMessage])

// INCORRECT — mutating state directly
messages.push(newMessage) // ❌
setMessages(messages)
```

### Effects (`useEffect`)
Effects are an escape hatch for synchronizing with **external systems** (network, DOM APIs, third-party widgets). If there is no external system involved, you probably don't need an Effect.

#### Prefer Derived Values Over Effects
If a value can be **calculated from existing props or state**, compute it during rendering. Do not store it in state and synchronize it with an Effect.

```tsx
// CORRECT — derive during render
function TodoList({ todos, filter }: TodoListProps) {
  const visibleTodos = todos.filter((t) => matchesFilter(t, filter))
  return <ul>{visibleTodos.map(/* ... */)}</ul>
}

// INCORRECT — redundant state + Effect
function TodoList({ todos, filter }: TodoListProps) {
  const [visibleTodos, setVisibleTodos] = useState<Todo[]>([])
  useEffect(() => {
    setVisibleTodos(todos.filter((t) => matchesFilter(t, filter))) // ❌
  }, [todos, filter])
}
```

For expensive calculations, use `useMemo` instead of `useEffect`:
```tsx
const visibleTodos = useMemo(
  () => getFilteredTodos(todos, filter),
  [todos, filter],
)
```

#### Event Logic vs. Effect Logic
- If code runs **because the user did something** (clicked, typed, submitted) → put it in an **event handler**.
- If code runs **because the component appeared on screen** → put it in an **Effect**.
- Do not chain multiple Effects that set state to trigger each other. Calculate the next state in the event handler instead.

```tsx
// CORRECT — event-specific logic in handler
async function handleSubmit() {
  await submitForm(data)
  showNotification('Saved!')
}

// INCORRECT — event logic in an Effect
useEffect(() => {
  if (submitted) {
    showNotification('Saved!') // ❌ runs on every render where submitted is true
  }
}, [submitted])
```

#### Reset State with `key`, Not Effects
To reset a component's state when a prop changes, pass that prop as a `key` to the component. Do not reset state in an Effect.

```tsx
// CORRECT — key forces React to recreate the component
<Profile userId={userId} key={userId} />

// INCORRECT — resetting state in an Effect
useEffect(() => {
  setComment('')
}, [userId]) // ❌
```

#### Cleanup & Race Conditions
- Always return a cleanup function from Effects that subscribe to external systems or fetch data, to prevent stale updates and memory leaks.
- For data fetching in Effects, use an `ignore` flag to handle race conditions.

```tsx
useEffect(() => {
  let ignore = false
  fetchResults(query).then((json) => {
    if (!ignore) setResults(json)
  })
  return () => { ignore = true }
}, [query])
```

#### Wrap Reusable Effects in Custom Hooks
Extract common Effect patterns (data fetching, subscriptions, timers) into custom hooks. The fewer raw `useEffect` calls in components, the easier the code is to maintain.

### Lists & Keys
- Always provide a `key` prop when rendering lists with `.map()`. Keys must be **unique among siblings** and **stable across renders**.
- Use a meaningful identifier from your data (database ID, unique name) — not the array index.
- Never generate keys on the fly (e.g., `key={Math.random()}`). This forces React to recreate DOM elements every render.
- `key` is not passed as a prop to the component. If the component needs the ID, pass it as a separate prop.

```tsx
// CORRECT — stable, unique ID from data
{messages.map((msg) => (
  <ChatMessage key={msg.id} message={msg} />
))}

// INCORRECT — array index as key (breaks on reorder/insert/delete)
{messages.map((msg, index) => (
  <ChatMessage key={index} message={msg} /> // ❌
))}
```

### State Management
- Use React built-in state (`useState`, `useReducer`, `useContext`) — no external state libraries.
- Lift state to the nearest common ancestor that needs it.
- For cross-cutting concerns (e.g., connection health, theme), use Context + Provider from `src/hooks/`.

### Error Boundaries
- Wrap major sections with `<ErrorBoundary>` from `react-error-boundary`.
- Provide a meaningful `fallback` or `FallbackComponent`.

## Fluent UI v9

### Imports
- Import components from `@fluentui/react-components`.
- Import icons from `@fluentui/react-icons`.
- Do NOT use Fluent UI v8 (`@fluentui/react`) packages.

```tsx
// CORRECT
import { Button, MessageBar, MessageBarBody, Text } from '@fluentui/react-components'
import { AddRegular, DeleteRegular } from '@fluentui/react-icons'

// INCORRECT — v8
import { PrimaryButton } from '@fluentui/react'
```

### Theming
- The app is wrapped in `<FluentProvider theme={...}>`. Never bypass the theme.
- Use Fluent UI design tokens (`tokens.*`) for all colors, spacing, typography, and radii. Do NOT hard-code color hex values or pixel spacing.

```tsx
import { tokens } from '@fluentui/react-components'

// CORRECT
backgroundColor: tokens.colorNeutralBackground2,
padding: tokens.spacingHorizontalL,
borderRadius: tokens.borderRadiusMedium,

// INCORRECT
backgroundColor: '#f5f5f5',
padding: '16px',
```

### Styling with `makeStyles`
- Use `makeStyles` from `@fluentui/react-components` for component-scoped styles.
- Each component's styles live in a co-located `<ComponentName>.styles.ts` file, exporting a `use<ComponentName>Styles` hook.
- Always use the tokens API inside `makeStyles`.
- Do NOT use inline `style` attributes except for truly dynamic values (e.g., computed positions).

```tsx
// ChatWindow.styles.ts
import { makeStyles, tokens } from '@fluentui/react-components'

export const useChatWindowStyles = makeStyles({
  root: {
    display: 'flex',
    height: '100%',
    backgroundColor: tokens.colorNeutralBackground2,
  },
  ribbon: {
    height: '48px',
    borderBottom: `1px solid ${tokens.colorNeutralStroke1}`,
    padding: `0 ${tokens.spacingHorizontalL}`,
  },
})

// ChatWindow.tsx
import { useChatWindowStyles } from './ChatWindow.styles'

export default function ChatWindow() {
  const styles = useChatWindowStyles()
  return <div className={styles.root}>...</div>
}
```

### Global CSS
- `src/styles/global.css` is for resets and base `body`/`#root` rules only.
- Do NOT add component styles to `global.css`.

## File & Folder Organization

### Directory Structure
```
src/
  components/<Feature>/          # Feature-grouped components
    ComponentName.tsx            # Component logic
    ComponentName.styles.ts      # makeStyles hook
    ComponentName.test.tsx       # Unit tests (co-located)
  hooks/                         # Custom React hooks
    useHookName.tsx
    useHookName.test.tsx
  services/                      # API client & error handling
    api.ts
    errors.ts
  types/                         # Shared TypeScript interfaces
    index.ts
  utils/                         # Pure helper functions
    helperName.ts
```

### File Naming
- Components: `PascalCase.tsx` (e.g., `ChatWindow.tsx`, `MessageList.tsx`)
- Styles: `PascalCase.styles.ts` (e.g., `ChatWindow.styles.ts`)
- Tests: `PascalCase.test.tsx` (co-located next to the source file)
- Hooks: `camelCase.tsx` (e.g., `useConnectionHealth.tsx`)
- Utils/services: `camelCase.ts` (e.g., `messageMapper.ts`, `api.ts`)

### Exports
- Use `export default` for the primary React component of a file (React convention for components).
- Use named exports for everything else (hooks, helpers, types, styles, service objects).
- Re-export shared types from `src/types/index.ts`.
- Do NOT use `export let` — exported values must not be mutable.
- Minimize the exported API surface: only export symbols used outside the module.

## API Layer

### Service Organization
- All API calls go through `src/services/api.ts` via the shared `apiClient` (Axios instance).
- Group related endpoints into exported objects (`healthApi`, `targetsApi`, `attacksApi`).
- Every API function MUST be `async` and return the response data directly.
- Use `encodeURIComponent` for user-supplied path segments to prevent injection.

```tsx
// CORRECT
export const targetsApi = {
  getTarget: async (name: string) =>
    await apiClient.get(`/targets/${encodeURIComponent(name)}`),
}
```

### Error Handling
- Normalize errors through `toApiError()` in `src/services/errors.ts`.
- Components should catch API errors and display user-friendly feedback via Fluent UI `MessageBar` — never expose raw error objects or stack traces to the UI.

## Code Quality

### No Magic Numbers / Strings
- Extract repeated values into named `CONSTANT_CASE` constants at the top of the file or in a shared constants file.
- Constants that could technically be mutated (e.g., objects, arrays) should still use `CONSTANT_CASE` to signal "do not modify".

```tsx
// CORRECT
const POLLING_INTERVAL_MS = 60_000
const MAX_RETRIES = 3
const UNIT_SUFFIXES = { milliseconds: 'ms', seconds: 's' } // CONSTANT_CASE: do not modify

// INCORRECT
setInterval(checkHealth, 60000)
```

### Iteration
- Use `for...of` to iterate arrays and iterables.
- Do NOT use `Array.prototype.forEach` — it obscures control flow and defeats some compiler checks (e.g., reachability, narrowing).
- Do NOT use `for...in` on arrays (gives string indices, not values).

```tsx
// CORRECT
for (const item of items) { ... }
for (const [key, value] of Object.entries(config)) { ... }

// INCORRECT
items.forEach((item) => { ... })
for (const i in items) { ... } // i is a string index!
```

### Arrow Functions in Expressions
- Always use arrow functions instead of `function` keyword in expressions (callbacks, inline handlers).
- Use block bodies (`=> { ... }`) when the return value is unused (e.g., `promise.then`).

```tsx
// CORRECT — return value used, expression body is fine
const filtered = items.filter((item) => item.active)

// CORRECT — return value unused, use block body
myPromise.then((v) => {
  console.log(v)
})

// INCORRECT — expression body when return value is unused
myPromise.then((v) => console.log(v))
```

### No `debugger` Statements
- `debugger` statements must not be committed to the codebase.

### Accessibility
- Use semantic HTML elements (`<nav>`, `<main>`, `<button>`, etc.) and Fluent UI components (which are accessible by default).
- Add `aria-label` or `aria-describedby` when the visual label is insufficient.
- Interactive elements MUST be keyboard-accessible. Use `data-testid` for test selectors, not DOM structure.

### Comments & Documentation
- Use `/** JSDoc */` for documentation that users of the code should read (exported functions, components, interfaces).
- Use `// line comments` for implementation notes that explain *why*, not *what*.
- Omit JSDoc type annotations (`@param {string}`, `@returns {number}`) — TypeScript already provides types. Only include `@param` / `@returns` when the description adds information beyond the name and type.
- Do not write comments that merely restate the parameter name or type.

```tsx
// CORRECT — adds information not obvious from the type
/**
 * Sends a prompt to the target and returns the response.
 * @param prompt - Must not exceed 4096 tokens.
 */
async function sendPrompt(prompt: string): Promise<Response> { ... }

// INCORRECT — restates what TypeScript already tells you
/**
 * @param prompt - The prompt string.
 * @returns A Promise of Response.
 */
```

## Final Checklist

Before committing frontend code, ensure:
- [ ] All function parameters have explicit TypeScript types; return types annotated on non-trivial functions
- [ ] No `any` types without a justifying comment — prefer `unknown`
- [ ] No `@ts-ignore` — use `@ts-expect-error` with a comment if absolutely needed
- [ ] `const` by default; `let` only when reassignment is needed; never `var`
- [ ] `===` / `!==` used for all comparisons (except `== null`)
- [ ] Naming follows conventions: `UpperCamelCase` for types/components, `lowerCamelCase` for variables/functions, `CONSTANT_CASE` for constants
- [ ] No `.forEach()` — use `for...of`
- [ ] No `debugger` statements
- [ ] Hooks called at top level only — not in conditions, loops, or nested functions
- [ ] Components are pure: no side effects during render, no mutating props/state
- [ ] Derived values computed during render — not stored in state and synced with `useEffect`
- [ ] Event-specific logic in event handlers — `useEffect` reserved for external system synchronization
- [ ] Effects that fetch data or subscribe include a cleanup function
- [ ] List items rendered with `.map()` have stable, unique `key` props (no array index)
- [ ] Fluent UI tokens used for all styling values — no hard-coded colors or spacing
- [ ] Styles are in a co-located `.styles.ts` file using `makeStyles`
- [ ] Shared types are in `src/types/index.ts`
- [ ] API calls go through `src/services/api.ts`
- [ ] `@/` alias used for cross-directory imports
- [ ] Components are accessible (keyboard, aria labels)
- [ ] `data-testid` attributes added for testable interactive elements
- [ ] No unused imports or variables (ESLint will catch this)
- [ ] JSDoc on exported symbols adds information beyond what TypeScript types already convey
