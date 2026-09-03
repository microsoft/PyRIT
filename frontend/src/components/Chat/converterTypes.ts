import type { ConverterConfigurationRequest, MessagePieceRequest } from '@/types'

export const PIECE_TYPE_TO_DATA_TYPE: Record<string, string> = {
  text: 'text',
  image: 'image_path',
  audio: 'audio_path',
  video: 'video_path',
  file: 'binary_path',
}

export interface PieceConversion {
  /** Ordered registry IDs of the converter pipeline that produced the value. */
  converterInstanceIds: string[]
  convertedValue: string
  originalValue: string
  /** Input piece type the conversion came from (e.g. 'text', 'image'). */
  pieceType: string
  /**
   * Backend data type of the converted value (e.g. 'text', 'image_path',
   * 'binary_path'). May differ from the input piece type when a converter
   * changes the data type — e.g. PDFConverter takes text and emits binary_path.
   */
  convertedDataType: string
}

export {
  basenameFromValue,
  buildMediaUrl,
  dataTypeToAttachmentKind,
  isPathDataType,
} from '@/utils/media'

/**
 * Turn per-modality pipelines into ordered REST converter configurations.
 *
 * Each configuration targets the exact original piece indexes of its modality
 * rather than relying on data-type filters, so a message with several pieces of
 * the same type has every one of them converted by the pipeline the operator
 * configured for that modality.
 */
export function buildRequestConverterConfigurations(
  pieces: MessagePieceRequest[],
  conversions: Record<string, PieceConversion>,
): ConverterConfigurationRequest[] {
  return Object.entries(conversions).flatMap(([pieceType, conversion]) => {
    const dataType = PIECE_TYPE_TO_DATA_TYPE[pieceType]
    if (!dataType || conversion.converterInstanceIds.length === 0) return []

    const indexesToApply = pieces.flatMap((piece: MessagePieceRequest, index: number) => (
      piece.data_type === dataType ? [index] : []
    ))
    if (indexesToApply.length === 0) return []

    return [{
      converter_ids: conversion.converterInstanceIds,
      indexes_to_apply: indexesToApply,
    }]
  })
}
