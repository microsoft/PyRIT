// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Tests for the UserTurnCard `⚡ Converter palette` affordance.
 *
 * Spec §2.2 UserTurnNode-specific: `⚡ Open converter palette (adds to
 * `params.converterPipeline`)` (V1.0). The lightning icon opens a
 * Fluent Menu listing available converters; clicking a converter
 * appends its id to `node.params.converterPipeline` via
 * `onSetUserTurnConverterPipeline(nodeId, newPipeline)`.
 *
 * V1.0 minimum: append-one-at-a-time. Removal + per-converter params
 * are V1.0.1. Available-converters list is host-supplied via a new
 * `availableConverters` TreeCanvas prop (host pre-fetches from the
 * `convertersApi.listConverters` route).
 *
 * Gates: ⚡ renders iff BOTH `onSetUserTurnConverterPipeline` callback
 * AND `availableConverters` (non-empty) are present.
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

const MOCK_CONVERTERS = [
  { id: 'base64', label: 'Base64 encoder' },
  { id: 'rot13', label: 'ROT13' },
  { id: 'leet', label: 'Leetspeak' },
]

describe('UserTurnCard — ⚡ Converter palette (spec §2.2)', () => {
  it('does NOT render ⚡ when onSetUserTurnConverterPipeline callback is missing', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r', { text: 'hi' })])
    const callbacks: ActionCallbacks = { onRefresh: jest.fn() }
    const { container } = render(
      <TreeCanvas
        tree={tree}
        actionCallbacks={callbacks}
        availableConverters={MOCK_CONVERTERS}
      />,
    )
    const card = findUserTurnCard(container, 'u')
    expect(within(card).queryByRole('button', { name: /converter palette/i })).toBeNull()
  })

  it('does NOT render ⚡ when availableConverters is empty', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline: jest.fn() }
    const { container } = render(
      <TreeCanvas tree={tree} actionCallbacks={callbacks} availableConverters={[]} />,
    )
    const card = findUserTurnCard(container, 'u')
    expect(within(card).queryByRole('button', { name: /converter palette/i })).toBeNull()
  })

  it('does NOT render ⚡ when availableConverters is omitted entirely', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline: jest.fn() }
    const { container } = render(
      <TreeCanvas tree={tree} actionCallbacks={callbacks} />,
    )
    const card = findUserTurnCard(container, 'u')
    expect(within(card).queryByRole('button', { name: /converter palette/i })).toBeNull()
  })

  it('renders ⚡ when both callback and non-empty availableConverters are wired', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline: jest.fn() }
    const { container } = render(
      <TreeCanvas
        tree={tree}
        actionCallbacks={callbacks}
        availableConverters={MOCK_CONVERTERS}
      />,
    )
    const card = findUserTurnCard(container, 'u')
    expect(within(card).getByRole('button', { name: /converter palette/i })).not.toBeNull()
  })

  it('clicking ⚡ opens a menu listing each available converter label', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline: jest.fn() }
    const { container } = render(
      <TreeCanvas
        tree={tree}
        actionCallbacks={callbacks}
        availableConverters={MOCK_CONVERTERS}
      />,
    )
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /converter palette/i }))
    // Fluent's Menu portals; query the document for the menu items.
    const items = Array.from(document.querySelectorAll('[role="menuitem"]'))
    const labels = items.map((i) => i.textContent ?? '')
    for (const c of MOCK_CONVERTERS) {
      expect(labels.some((l) => l.includes(c.label))).toBe(true)
    }
  })

  it('clicking a converter appends it to the pipeline via the callback', () => {
    const onSetUserTurnConverterPipeline = jest.fn()
    const tree = mkTree('r', [
      mkRoot('r'),
      mkUserTurn('u', 'r', { converterPipeline: [{ converterId: 'rot13' }] }),
    ])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline }
    const { container } = render(
      <TreeCanvas
        tree={tree}
        actionCallbacks={callbacks}
        availableConverters={MOCK_CONVERTERS}
      />,
    )
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /converter palette/i }))
    const base64Item = Array.from(document.querySelectorAll('[role="menuitem"]')).find(
      (i) => i.textContent?.includes('Base64 encoder'),
    ) as HTMLElement
    expect(base64Item).toBeDefined()
    fireEvent.click(base64Item)
    expect(onSetUserTurnConverterPipeline).toHaveBeenCalledTimes(1)
    expect(onSetUserTurnConverterPipeline).toHaveBeenCalledWith(
      nodeId('u'),
      [{ converterId: 'rot13' }, { converterId: 'base64' }],
    )
  })

  it('clicking a converter with an empty pipeline yields a single-element list', () => {
    const onSetUserTurnConverterPipeline = jest.fn()
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r')])
    const callbacks: ActionCallbacks = { onSetUserTurnConverterPipeline }
    const { container } = render(
      <TreeCanvas
        tree={tree}
        actionCallbacks={callbacks}
        availableConverters={MOCK_CONVERTERS}
      />,
    )
    const card = findUserTurnCard(container, 'u')
    fireEvent.click(within(card).getByRole('button', { name: /converter palette/i }))
    const leetItem = Array.from(document.querySelectorAll('[role="menuitem"]')).find(
      (i) => i.textContent?.includes('Leetspeak'),
    ) as HTMLElement
    fireEvent.click(leetItem)
    expect(onSetUserTurnConverterPipeline).toHaveBeenCalledWith(nodeId('u'), [
      { converterId: 'leet' },
    ])
  })
})
