// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

import { render, screen } from '@testing-library/react'
import type React from 'react'

import { TreeCanvas } from './TreeCanvas'
import { mkRoot, mkSend, mkTree, mkUserTurn } from '../../runner/testHelpers'

jest.mock('@xyflow/react', () => ({
  Controls: () => <div data-testid="mock-controls" />,
  MiniMap: () => <div data-testid="mock-minimap" />,
  ReactFlowProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  ReactFlow: (props: {
    children?: React.ReactNode
    nodesDraggable?: boolean
    nodesConnectable?: boolean
    edgesFocusable?: boolean
    onNodesChange?: unknown
  }) => (
    <div
      data-testid="mock-react-flow"
      data-nodes-draggable={String(props.nodesDraggable)}
      data-nodes-connectable={String(props.nodesConnectable)}
      data-edges-focusable={String(props.edgesFocusable)}
      data-has-on-nodes-change={String(typeof props.onNodesChange === 'function')}
    >
      {props.children}
    </div>
  ),
}))

describe('TreeCanvas — ReactFlow interaction props', () => {
  it('enables local node dragging but keeps ad-hoc connecting and edge tab stops disabled', () => {
    const tree = mkTree('r', [mkRoot('r'), mkUserTurn('u', 'r'), mkSend('s', 'u')])

    render(<TreeCanvas tree={tree} />)

    const flow = screen.getByTestId('mock-react-flow')
    expect(flow).toHaveAttribute('data-nodes-draggable', 'true')
    expect(flow).toHaveAttribute('data-has-on-nodes-change', 'true')
    expect(flow).toHaveAttribute('data-nodes-connectable', 'false')
    expect(flow).toHaveAttribute('data-edges-focusable', 'false')
    expect(screen.getByTestId('mock-controls')).toBeInTheDocument()
    expect(screen.getByTestId('mock-minimap')).toBeInTheDocument()
  })
})