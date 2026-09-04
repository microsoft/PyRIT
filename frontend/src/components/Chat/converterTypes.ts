export const PIECE_TYPE_TO_DATA_TYPE: Record<string, string> = {
  text: 'text',
  image: 'image_path',
  audio: 'audio_path',
  video: 'video_path',
  file: 'binary_path',
}

export interface PieceConversion {
  converterInstanceId: string
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
