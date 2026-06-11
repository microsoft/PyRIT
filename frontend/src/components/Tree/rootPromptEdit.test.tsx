// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the RootPromptCard inline-edit affordance.
 *
 * Spec §2.2 RootPromptNode-specific: `✏ Edit prompt + target + system
 * prompt (inline editor)` (V1.0). The pencil opens a multi-field
 * editor (text textarea + systemPrompt textarea + targetRegistryName
 * input) with Save/Cancel. Save fires
 * `onEditRootPromptParams(nodeId, patch)` where patch is a Partial of
 * the RootPrompt params.
 *
 * Per-field validation lives in the host (no V1.0 inline error
 * surface; that's V1.0.1). The card just collects + dispatches.
 */

import { fireEvent, render, within } from '@testing-library/react'

import { TreeCanvas } from './TreeCanvas'
import type { ActionCallbacks } from './actionRail'
import { findCard, mkRoot, mkTree, nodeId } from '../../runner/testHelpers'

const findRootCard = (container: HTMLElement, id: string) => findCard(container, id)

describe('RootPromptCard — inline edit affordance (spec §2.2)', () => {
  it('does NOT render ✏ Edit button when onEditRootPromptParams callback is missing', () => {
    const tree = mkTree('r', [mkRoot('r', { text: 'hi' })])
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    expect(within(card).queryByRole('button', { name: /edit root/i })).toBeNull()
  })

  it('renders ✏ Edit button when onEditRootPromptParams callback is wired', () => {
    const tree = mkTree('r', [mkRoot('r', { text: 'hi' })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    expect(within(card).getByRole('button', { name: /edit root/i })).not.toBeNull()
  })

  it('clicking ✏ enters edit mode: three fields visible, read-only body hidden', () => {
    const tree = mkTree('r', [
      mkRoot('r', {
        text: 'original',
        systemPrompt: 'be helpful',
        targetRegistryName: 'openai-gpt-4',
      }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    expect(within(card).queryByRole('textbox', { name: /prompt text/i })).toBeNull()
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const promptField = within(card).getByRole('textbox', { name: /prompt text/i }) as HTMLTextAreaElement
    const systemField = within(card).getByRole('textbox', { name: /system prompt/i }) as HTMLTextAreaElement
    const targetField = within(card).getByRole('textbox', { name: /target/i }) as HTMLInputElement
    expect(promptField.value).toBe('original')
    expect(systemField.value).toBe('be helpful')
    expect(targetField.value).toBe('openai-gpt-4')
  })

  it('Save fires onEditRootPromptParams(nodeId, { text, systemPrompt, targetRegistryName })', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r', {
        text: 'old',
        systemPrompt: 'old sys',
        targetRegistryName: 'old-target',
      }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const promptField = within(card).getByRole('textbox', { name: /prompt text/i }) as HTMLTextAreaElement
    const systemField = within(card).getByRole('textbox', { name: /system prompt/i }) as HTMLTextAreaElement
    const targetField = within(card).getByRole('textbox', { name: /target/i }) as HTMLInputElement
    fireEvent.change(promptField, { target: { value: 'new text' } })
    fireEvent.change(systemField, { target: { value: 'new sys' } })
    fireEvent.change(targetField, { target: { value: 'new-target' } })
    fireEvent.click(within(card).getByRole('button', { name: /^save$/i }))
    expect(onEditRootPromptParams).toHaveBeenCalledTimes(1)
    expect(onEditRootPromptParams).toHaveBeenCalledWith(nodeId('r'), {
      text: 'new text',
      systemPrompt: 'new sys',
      targetRegistryName: 'new-target',
    })
  })

  it('Cancel discards (no callback, body restored)', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r', { text: 'keep me', targetRegistryName: 't' }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const promptField = within(card).getByRole('textbox', { name: /prompt text/i }) as HTMLTextAreaElement
    fireEvent.change(promptField, { target: { value: 'discard me' } })
    fireEvent.click(within(card).getByRole('button', { name: /^cancel$/i }))
    expect(onEditRootPromptParams).not.toHaveBeenCalled()
    expect(within(card).queryByRole('textbox', { name: /prompt text/i })).toBeNull()
    expect(within(card).getByText('keep me')).not.toBeNull()
  })

  it('Esc on the prompt textarea cancels', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [mkRoot('r', { text: 'esc' })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const promptField = within(card).getByRole('textbox', { name: /prompt text/i })
    fireEvent.keyDown(promptField, { key: 'Escape' })
    expect(onEditRootPromptParams).not.toHaveBeenCalled()
    expect(within(card).queryByRole('textbox', { name: /prompt text/i })).toBeNull()
  })

  it('Cmd-Enter on the prompt textarea saves with current draft state', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r', { text: 'a', systemPrompt: 'b', targetRegistryName: 'c' }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const promptField = within(card).getByRole('textbox', { name: /prompt text/i }) as HTMLTextAreaElement
    fireEvent.change(promptField, { target: { value: 'kbd-saved' } })
    fireEvent.keyDown(promptField, { key: 'Enter', ctrlKey: true })
    expect(onEditRootPromptParams).toHaveBeenCalledWith(nodeId('r'), {
      text: 'kbd-saved',
      systemPrompt: 'b',
      targetRegistryName: 'c',
    })
  })

  it('initializes systemPrompt textarea to empty string when params.systemPrompt is undefined', () => {
    const tree = mkTree('r', [mkRoot('r', { text: 't', systemPrompt: undefined })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams: jest.fn() }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const systemField = within(card).getByRole('textbox', { name: /system prompt/i }) as HTMLTextAreaElement
    expect(systemField.value).toBe('')
  })

  it('Cmd-Enter on the SYSTEM PROMPT textarea saves (uniform keyboard contract across fields)', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r', { text: 'a', systemPrompt: 'b', targetRegistryName: 'c' }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const systemField = within(card).getByRole('textbox', { name: /system prompt/i }) as HTMLTextAreaElement
    fireEvent.change(systemField, { target: { value: 'system-edited' } })
    fireEvent.keyDown(systemField, { key: 'Enter', ctrlKey: true })
    expect(onEditRootPromptParams).toHaveBeenCalledWith(nodeId('r'), {
      text: 'a',
      systemPrompt: 'system-edited',
      targetRegistryName: 'c',
    })
  })

  it('Cmd-Enter on the TARGET input saves', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r', { text: 'a', systemPrompt: 'b', targetRegistryName: 'c' }),
    ])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const targetField = within(card).getByRole('textbox', { name: /target/i }) as HTMLInputElement
    fireEvent.change(targetField, { target: { value: 'target-edited' } })
    fireEvent.keyDown(targetField, { key: 'Enter', ctrlKey: true })
    expect(onEditRootPromptParams).toHaveBeenCalledWith(nodeId('r'), {
      text: 'a',
      systemPrompt: 'b',
      targetRegistryName: 'target-edited',
    })
  })

  it('Esc on the SYSTEM PROMPT textarea cancels', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [mkRoot('r', { text: 'esc' })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const systemField = within(card).getByRole('textbox', { name: /system prompt/i })
    fireEvent.keyDown(systemField, { key: 'Escape' })
    expect(onEditRootPromptParams).not.toHaveBeenCalled()
    expect(within(card).queryByRole('textbox', { name: /prompt text/i })).toBeNull()
  })

  it('Esc on the TARGET input cancels', () => {
    const onEditRootPromptParams = jest.fn()
    const tree = mkTree('r', [mkRoot('r', { text: 'esc' })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams }
    const { container } = render(<TreeCanvas tree={tree} actionCallbacks={callbacks} />)
    const card = findRootCard(container, 'r')
    fireEvent.click(within(card).getByRole('button', { name: /edit root/i }))
    const targetField = within(card).getByRole('textbox', { name: /target/i })
    fireEvent.keyDown(targetField, { key: 'Escape' })
    expect(onEditRootPromptParams).not.toHaveBeenCalled()
    expect(within(card).queryByRole('textbox', { name: /prompt text/i })).toBeNull()
  })

  it('updated node.params re-render the read-mode body after host save', () => {
    const tree1 = mkTree('r', [mkRoot('r', { text: 'before' })])
    const callbacks: ActionCallbacks = { onEditRootPromptParams: jest.fn() }
    const { container, rerender } = render(
      <TreeCanvas tree={tree1} actionCallbacks={callbacks} />,
    )
    expect(within(findRootCard(container, 'r')).getByText('before')).not.toBeNull()
    const tree2 = mkTree('r', [mkRoot('r', { text: 'after' })], { id: tree1.id })
    rerender(<TreeCanvas tree={tree2} actionCallbacks={callbacks} />)
    expect(within(findRootCard(container, 'r')).getByText('after')).not.toBeNull()
  })
})
