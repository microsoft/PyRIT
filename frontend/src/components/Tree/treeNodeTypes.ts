// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Kind → react-flow node component map. Passed to
 * `<ReactFlow nodeTypes={treeNodeTypes} />`.
 *
 * Lives in its own module so eslint's react-refresh/only-export-components
 * rule stays happy (mixing component exports with non-component exports
 * defeats HMR for the components).
 */

import {
  FanCard,
  ImportMessageCard,
  RootPromptCard,
  ScoreCard,
  SendCard,
  UserTurnCard,
} from './nodeCards'

export const treeNodeTypes = {
  root_prompt: RootPromptCard,
  import_message: ImportMessageCard,
  user_turn: UserTurnCard,
  send: SendCard,
  fan: FanCard,
  score: ScoreCard,
} as const
