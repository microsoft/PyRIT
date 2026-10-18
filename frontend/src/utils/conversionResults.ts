import type { ConverterInputPiece, ConverterPreviewResponse, ConverterPreviewStep, PieceConversion } from '@/types'

/** Converter IDs record stages used, including stages with manually edited output. */
export function buildAppliedConversions(
  inputs: ConverterInputPiece[],
  results: Record<string, ConverterPreviewResponse>,
): Record<string, PieceConversion> {
  const applied: Record<string, PieceConversion> = {}
  for (const input of inputs) {
    const result = results[input.id]
    if (!result) continue
    applied[input.id] = {
      pieceId: input.id,
      pieceType: input.pieceType,
      converterInstanceIds: result.steps.map((step: ConverterPreviewStep) => step.converter_id),
      originalValue: input.value,
      convertedValue: result.converted_value,
      convertedDataType: result.converted_value_data_type,
    }
  }
  return applied
}
