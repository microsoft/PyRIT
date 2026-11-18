import { useEffect, useState } from 'react'

import type { AttachmentUploadOutcome, MessageAttachment } from '@/types'
import { startAttachmentUpload, subscribeAttachmentUpload } from '@/utils/messageMapper'

/** Upload draft files without blocking editing or changing draft identity. */
export function useAttachmentUploads(attachments: MessageAttachment[]) {
  const [outcomes, setOutcomes] = useState(new Map<string, AttachmentUploadOutcome>())
  useEffect(() => {
    const unsubscribe: Array<() => void> = []
    for (const attachment of attachments) {
      const file = attachment.file
      if (!file) continue
      const id = attachment.draftId ?? attachment.url
      unsubscribe.push(subscribeAttachmentUpload(file, (outcome: AttachmentUploadOutcome | undefined) => {
        setOutcomes((previous: Map<string, AttachmentUploadOutcome>) => {
          const next = new Map(previous)
          if (outcome) next.set(id, outcome)
          else next.delete(id)
          return next
        })
      }))
    }
    return () => { for (const stop of unsubscribe) stop() }
  }, [attachments])

  const retry = (file: File): void => {
    void startAttachmentUpload(file, true)
  }
  return {
    pending: attachments.some((attachment: MessageAttachment) => attachment.file && !outcomes.has(attachment.draftId ?? attachment.url)),
    errors: attachments.flatMap((attachment: MessageAttachment) => {
      const file = attachment.file
      if (!file) return []
      const outcome = outcomes.get(attachment.draftId ?? attachment.url)
      return outcome?.status === 'failed' ? [{ id: attachment.draftId ?? attachment.url, file, detail: outcome.detail }] : []
    }),
    retry,
  }
}
