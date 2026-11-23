import type { UserEvent } from '@testing-library/user-event'

/**
 * Replaces a text field's value in one paste. Typing a long value key by key
 * re-renders large forms once per character, which is slow on busy runners.
 */
export async function replaceText(user: UserEvent, field: HTMLElement, text: string): Promise<void> {
  await user.clear(field)
  await user.paste(text)
}
