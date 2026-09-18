import type { ConverterConfigurationRequest, ConverterInputPiece, MessageAttachment, PieceConversion } from '@/types'
import { mimeTypeToDataType } from '@/utils/messageMapper'

export const PIECE_TYPE_TO_DATA_TYPE: Record<string, string> = {
  text: 'text',
  image: 'image_path',
  audio: 'audio_path',
  video: 'video_path',
  file: 'binary_path',
}

export type { PieceConversion } from '@/types'

export {
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
} from '@/utils/media'

export function withDraftIdentity(attachment: MessageAttachment): MessageAttachment {
  return { ...attachment, draftId: attachment.draftId ?? crypto.randomUUID() }
}

export function buildConverterInputs(text: string, attachments: MessageAttachment[]): ConverterInputPiece[] {
  return [
    { id: 'text', pieceType: 'text', name: 'Text', dataType: 'text', value: text },
    ...attachments.map((attachment: MessageAttachment): ConverterInputPiece => {
      if (!attachment.draftId) throw new Error('Draft attachment is missing its identity.')
      return {
        id: attachment.draftId,
        pieceType: attachment.type,
        name: attachment.name,
        dataType: attachment.sourceDataType ?? mimeTypeToDataType(attachment.mimeType),
        value: attachment.sourceValue ?? attachment.url,
        file: attachment.file,
      }
    }),
  ]
}

/** Match buildMessagePieces ordering, including its omission of empty text. */
export function buildDraftPieceIds(text: string, attachments: MessageAttachment[]): string[] {
  return buildConverterInputs(text, attachments)
    .filter((input: ConverterInputPiece) => input.id !== 'text' || text.trim().length > 0)
    .map((input: ConverterInputPiece) => input.id)
}

/** Target only applied piece identities, in their final request order. */
export function buildRequestConverterConfigurations(
  pieceIds: string[],
  conversions: Record<string, PieceConversion>,
): ConverterConfigurationRequest[] {
  return pieceIds.flatMap((pieceId: string, index: number) => {
    const conversion = conversions[pieceId]
    if (!conversion || conversion.converterInstanceIds.length === 0) return []
    return [{
      converter_ids: conversion.converterInstanceIds,
      indexes_to_apply: [index],
    }]
  })
}
