import { useEffect, useState } from 'react'

import type { AttachmentUploadOutcome, MessageAttachment } from '@/types'
import { startAttachmentUpload } from '@/utils/messageMapper'

/** Upload draft files without blocking editing or changing draft identity. */
export function useAttachmentUploads(attachments: MessageAttachment[]) {
  const [outcomes, setOutcomes] = useState(new Map<string, AttachmentUploadOutcome>())
  const [retryRevision, setRetryRevision] = useState(0)
  useEffect(() => {
    let active = true
    for (const attachment of attachments) {
      const file = attachment.file
      if (!file) continue
      const id = attachment.draftId ?? attachment.url
      void startAttachmentUpload(file).then((outcome: AttachmentUploadOutcome) => {
        if (active) setOutcomes((previous: Map<string, AttachmentUploadOutcome>) => new Map(previous).set(id, outcome))
      })
    }
    return () => { active = false }
  }, [attachments, retryRevision])

  const retry = (file: File): void => {
    void startAttachmentUpload(file, true)
    setOutcomes((previous: Map<string, AttachmentUploadOutcome>) => {
      const next = new Map(previous)
      for (const attachment of attachments) {
        if (attachment.file === file) next.delete(attachment.draftId ?? attachment.url)
      }
      return next
    })
    setRetryRevision((previous: number) => previous + 1)
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
