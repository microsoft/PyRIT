import { useEffect, useState, type DragEvent, type ReactNode } from 'react'

import { Text, mergeClasses } from '@fluentui/react-components'
import { AttachRegular } from '@fluentui/react-icons'

import { useFileDropZoneStyles } from './FileDropZone.styles'

interface FileDropZoneProps {
  children: ReactNode
  onFiles: (files: File[]) => void
  disabled?: boolean
  className?: string
  label?: string
  testId?: string
}

export default function FileDropZone({
  children, onFiles, disabled = false, className, label = 'Drop files to attach', testId,
}: FileDropZoneProps) {
  const styles = useFileDropZoneStyles()
  const [dragDepth, setDragDepth] = useState(0)
  const dragging = dragDepth > 0
  const isFileDrag = (event: DragEvent<HTMLDivElement>): boolean => Array.from(event.dataTransfer.types).includes('Files')

  if (disabled && dragging) setDragDepth(0)

  useEffect(() => {
    if (!dragging) return
    const reset = (): void => { setDragDepth(0) }
    const cancel = (event: KeyboardEvent): void => { if (event.key === 'Escape') reset() }
    document.addEventListener('drop', reset, true)
    document.addEventListener('dragend', reset, true)
    document.addEventListener('keydown', cancel, true)
    window.addEventListener('blur', reset)
    return () => {
      document.removeEventListener('drop', reset, true)
      document.removeEventListener('dragend', reset, true)
      document.removeEventListener('keydown', cancel, true)
      window.removeEventListener('blur', reset)
    }
  }, [dragging])

  return <div
    className={mergeClasses(styles.root, className)}
    data-testid={testId}
    onDragEnter={(event: DragEvent<HTMLDivElement>) => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      event.stopPropagation()
      if (!disabled) setDragDepth((depth: number) => depth + 1)
    }}
    onDragOver={(event: DragEvent<HTMLDivElement>) => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = disabled ? 'none' : 'copy'
    }}
    onDragLeave={(event: DragEvent<HTMLDivElement>) => {
      if (!isFileDrag(event) && !dragging) return
      event.stopPropagation()
      setDragDepth((depth: number) => Math.max(0, depth - 1))
    }}
    onDrop={(event: DragEvent<HTMLDivElement>) => {
      if (!isFileDrag(event)) return
      event.preventDefault()
      event.stopPropagation()
      setDragDepth(0)
      if (!disabled && event.dataTransfer.files.length) onFiles(Array.from(event.dataTransfer.files))
    }}
  >
    {children}
    {dragging && !disabled && <div className={styles.feedback} role="status">
      <div className={styles.prompt}>
        <AttachRegular />
        <Text weight="semibold">{label}</Text>
      </div>
    </div>}
  </div>
}
