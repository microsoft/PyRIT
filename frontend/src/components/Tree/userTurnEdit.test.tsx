// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the UserTurnCard inline-edit affordance.
 *
 * Spec §2.2 UserTurnNode-specific: `✏ Edit text inline` (V1.0). The
 * pencil icon lives in the per-node action rail (rendered alongside
 * the common Refresh/Branch/etc. actions); clicking it swaps the card
 * body to a `<Textarea>` with Save/Cancel. Save fires the
 * `onEditUserTurnText(nodeId, newText)` callback; Cancel discards.
 *
 * Pinned contracts:
 *   - card without `onEditUserTurnText` callback does NOT show ✏
 *   - clicking ✏ enters edit mode (textarea visible, read-only body
 *     hidden)
 *   - typing updates the local draft
 *   - clicking Save fires the callback with (nodeId, draftText)
 *   - clicking Cancel exits edit mode without firing
 *   - pressing Esc cancels
 *   - pressing Cmd/Ctrl-Enter saves
 *   - the read-mode body reflects an updated `node.params.text` prop
 *     (the host owns the source of truth; the card re-renders with
 *     the new prop after the save handler updates the tree)
 */

import { fireEvent, render, within } from '@testing-library/react'

import { TreeCanvas } from './TreeCanvas'
import type { ActionCallbacks } from './actionRail'
import {
  findCard,
  mkRoot,
  mkTree,
  mkUserTurn,
  nodeId,
} from '../../runner/testHelpers'

const findUserTurnCard = (container: HTMLElement, id: string) => findCard(container, id)

describe('UserTurnCard — inline edit affordance (spec §2.2)', () => {
  it('does NOT render ✏ Edit button when onEditUserTurnText callback is missing', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'hi' })])
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    expect(card).not.toBeNull()
    expect(within(card).queryByRole('button', { name: /edit text/i })).toBeNull()
  })

  it('renders ✏ Edit button when onEditUserTurnText callback is wired', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'hi' })])
    const callbacks: ActionCallbacks = {
      onRefresh: jest.fn(),
      onEditUserTurnText: jest.fn(),
    }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    expect(within(card).getByRole('button', { name: /edit text/i })).not.toBeNull()
  })

  it('clicking ✏ enters edit mode: textarea visible, read-only body hidden', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'original text' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    // Read mode by default
    expect(within(card).queryByRole('textbox')).toBeNull()
    expect(within(card).getByText('original text')).not.toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: /edit text/i }))
    // Edit mode: textarea visible
    const textarea = within(card).getByRole('textbox') as HTMLTextAreaElement
    expect(textarea).not.toBeNull()
    expect(textarea.value).toBe('original text')
  })

  it('clicking Save fires onEditUserTurnText(nodeId, draftText)', async () => {
    const onEditUserTurnText = jest.fn()
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'hello' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /edit text/i }))
    const textarea = within(card).getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'hello world' } })
    fireEvent.click(within(card).getByRole('button', { name: /^save$/i }))
    expect(onEditUserTurnText).toHaveBeenCalledTimes(1)
    expect(onEditUserTurnText).toHaveBeenCalledWith(nodeId('u'), 'hello world')
  })

  it('clicking Cancel exits edit mode without firing the callback', () => {
    const onEditUserTurnText = jest.fn()
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'keep me' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /edit text/i }))
    const textarea = within(card).getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'discard me' } })
    fireEvent.click(within(card).getByRole('button', { name: /^cancel$/i }))
    expect(onEditUserTurnText).not.toHaveBeenCalled()
    // Read mode again: original text restored
    expect(within(card).queryByRole('textbox')).toBeNull()
    expect(within(card).getByText('keep me')).not.toBeNull()
  })

  it('pressing Esc cancels (no callback, body restored)', () => {
    const onEditUserTurnText = jest.fn()
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'esc me' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /edit text/i }))
    const textarea = within(card).getByRole('textbox')
    fireEvent.keyDown(textarea, { key: 'Escape' })
    expect(onEditUserTurnText).not.toHaveBeenCalled()
    expect(within(card).queryByRole('textbox')).toBeNull()
  })

  it('pressing Ctrl-Enter saves (no plain-Enter conflict with multi-line input)', () => {
    const onEditUserTurnText = jest.fn()
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'kbd' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /edit text/i }))
    const textarea = within(card).getByRole('textbox') as HTMLTextAreaElement
    fireEvent.change(textarea, { target: { value: 'saved via shortcut' } })
    fireEvent.keyDown(textarea, { key: 'Enter', ctrlKey: true })
    expect(onEditUserTurnText).toHaveBeenCalledWith(nodeId('u'), 'saved via shortcut')
  })

  it('Edit button has aria-label identifying the action for screen readers', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'hi' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findUserTurnCard(container, 'u')
    const btn = within(card).getByRole('button', { name: /edit text/i })
    expect(btn.getAttribute('aria-label')).toMatch(/edit text/i)
  })

  it('updated node.params.text re-renders the read-mode body after a host-side save', () => {
    // Host owns the source of truth: when the host updates the tree
    // after a save, the card receives the new node prop and re-renders.
    const tree1 = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'before' })])
    const callbacks: ActionCallbacks = { onEditUserTurnText: jest.fn() }
    const { container, rerender } = render(
      <TreeCanvas tree={tree1} actionCallbacks={callbacks} />,
    )
    const card1 = findUserTurnCard(container, 'u')
    expect(within(card1).getByText('before')).not.toBeNull()
    const tree2 = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'after' })], {
      id: tree1.id,
    })
    rerender(<TreeCanvas tree={tree2} actionCallbacks={callbacks} />)
    const card2 = findUserTurnCard(container, 'u')
    expect(within(card2).getByText('after')).not.toBeNull()
  })
})
