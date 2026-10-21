import { useRef, useState } from 'react'

import type { Message } from '@/types'
import { exportConversation } from '@/utils/conversationExport'
import type { ExportFormat } from '@/utils/conversationExport'

export interface UseConversationExportOptions {
  messages: Message[]
  conversationId: string | null
  /** True while the transcript is not stable: sending or loading. */
  blocked: boolean
}

export interface ConversationExport {
  canExport: boolean
  isExporting: boolean
  exportAs: (format: ExportFormat) => Promise<void>
}

export function useConversationExport({
  messages,
  conversationId,
  blocked,
}: UseConversationExportOptions): ConversationExport {
  const [isExporting, setIsExporting] = useState(false)
  const isExportingRef = useRef(false)

  // Export is available whenever there is a stable, viewable conversation:
  // not while empty, loading, or mid-send. A lone system prompt (rendered only
  // in the banner, not the chat body) does not count as an exportable message.
  // Read-only / operator-lock / cross-target states do not block export.
  const canExport = messages.some((message) => !message.isLoading && message.role !== 'system') && !blocked

  const exportAs = async (format: ExportFormat): Promise<void> => {
    // A ref, not the state flag: two clicks in the same tick would both read
    // the pre-render value and start duplicate exports.
    if (isExportingRef.current) {
      return
    }
    isExportingRef.current = true
    setIsExporting(true)
    try {
      await exportConversation({ messages, conversationId, format })
    } catch (err) {
      console.error('Failed to export conversation:', err)
    } finally {
      isExportingRef.current = false
      setIsExporting(false)
    }
  }

  return { canExport, isExporting, exportAs }
}
