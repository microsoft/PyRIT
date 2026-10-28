import type { ConverterInstance } from '@/types'

const OUTPUT_TYPE_ORDER = ['text', 'image_path', 'audio_path', 'video_path', 'binary_path']

/** Group registered converters consistently in chat and technique pickers. */
export function groupConverters(converters: ConverterInstance[]): Array<{ type: string; converters: ConverterInstance[] }> {
  const groups = new Map<string, ConverterInstance[]>()
  for (const converter of converters) {
    const type = converter.identifier.supported_output_types?.[0] ?? 'text'
    const group = groups.get(type) ?? []
    group.push(converter)
    groups.set(type, group)
  }
  return [...OUTPUT_TYPE_ORDER, ...[...groups.keys()].filter((type) => !OUTPUT_TYPE_ORDER.includes(type))]
    .filter((type) => groups.has(type))
    .map((type) => ({ type, converters: groups.get(type) ?? [] }))
}
