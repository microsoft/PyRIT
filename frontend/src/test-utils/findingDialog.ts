import { screen, within } from '@testing-library/react'
import type { UserEvent } from '@testing-library/user-event'

/** Opens a finding field's dropdown by its label and picks the option with this exact name. */
export async function pick(user: UserEvent, label: string, optionName: string): Promise<void> {
  await user.click(screen.getByRole('combobox', { name: label }))
  await user.click(within(await screen.findByRole('listbox')).getByRole('option', { name: optionName }))
}

// The confirmation's title wraps the key in <code>, so it is found by its Keep it button instead of its name.
export async function findRemovalConfirmation(): Promise<HTMLElement> {
  const dialogs = await screen.findAllByRole('dialog')
  const confirmation = dialogs.find((dialog: HTMLElement) => within(dialog).queryByRole('button', { name: 'Keep it' }))
  if (confirmation === undefined) throw new Error('No removal confirmation is open')
  return confirmation
}
