// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Uniform keyboard contract for inline editors: Esc cancels, Cmd/Ctrl-Enter
 * saves. Used by InlineTextEditor (UserTurn) and InlineRootPromptEditor's
 * three fields so every editable surface across the canvas honors the same
 * shortcuts. PR5h.9 extraction — pre-PR5h.9, InlineRootPromptEditor only
 * handled Cmd-Enter on the prompt textarea; systemPrompt + target fields
 * were silent for the shortcut. The hook removes the bug-by-omission by
 * construction.
 */

import { useCallback } from 'react'
import type { KeyboardEvent } from 'react'

export interface EditorKeyboardOptions {
  onSave: () => void
  onCancel: () => void
}

export function useEditorKeyboard(
  opts: EditorKeyboardOptions,
): (e: KeyboardEvent<HTMLElement>) => void {
  const { onSave, onCancel } = opts
  return useCallback(
    (e: KeyboardEvent<HTMLElement>) => {
      if (e.key === 'Escape') {
        e.preventDefault()
        onCancel()
      } else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault()
        onSave()
      }
    },
    [onSave, onCancel],
  )
}
