import type {
  ConverterInputPiece, MessageAttachment, MessagePieceRequest, PieceConversion,
} from '@/types'
import { generateClientId } from '@/utils/clientId'
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
  return { ...attachment, draftId: attachment.draftId ?? generateClientId() }
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

/** Match request piece ordering, retaining empty original text when it has an applied result. */
export function buildDraftPieceIds(
  text: string,
  attachments: MessageAttachment[],
  conversions: Record<string, PieceConversion> = {},
): string[] {
  return buildConverterInputs(text, attachments)
    .filter((input: ConverterInputPiece) => input.id !== 'text' || text.trim().length > 0 || conversions.text !== undefined)
    .map((input: ConverterInputPiece) => input.id)
}

/** Attach applied results by draft identity without changing each piece's original value. */
export function applyConvertedValues(
  pieces: MessagePieceRequest[],
  pieceIds: string[],
  conversions: Record<string, PieceConversion>,
): MessagePieceRequest[] {
  if (Object.keys(conversions).length === 0) return pieces
  if (pieces.length !== pieceIds.length) throw new Error('Message pieces do not match the draft identities.')
  return pieces.map((piece: MessagePieceRequest, index: number): MessagePieceRequest => {
    const conversion = conversions[pieceIds[index]]
    return conversion ? {
      ...piece,
      converted_value: conversion.convertedValue,
      converted_value_data_type: conversion.convertedDataType,
      applied_converter_ids: conversion.converterInstanceIds,
    } : piece
  })
}
