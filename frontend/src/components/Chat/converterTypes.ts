export const PIECE_TYPE_TO_DATA_TYPE: Record<string, string> = {
  text: 'text',
  image: 'image_path',
  audio: 'audio_path',
  video: 'video_path',
}

export interface PieceConversion {
  converterInstanceId: string
  convertedValue: string
  originalValue: string
  pieceType: string
}
