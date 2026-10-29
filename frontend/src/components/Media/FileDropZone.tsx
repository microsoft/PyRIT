import { useCallback, useEffect, useRef, useState, type DragEvent, type ReactNode } from 'react'

import { Text, mergeClasses } from '@fluentui/react-components'
import { AttachRegular } from '@fluentui/react-icons'

import { isFileDrag } from '@/utils/fileDrag'

import { useFileDropZoneStyles } from './FileDropZone.styles'

interface FileDropZoneProps {
  children: ReactNode
  onFiles: (files: File[]) => void
  disabled?: boolean
  enabled?: boolean
  className?: string
  label?: string
  testId?: string
}

export default function FileDropZone({
  children, onFiles, disabled = false, enabled = true, className, label = 'Drop files to attach', testId,
}: FileDropZoneProps) {
  const styles = useFileDropZoneStyles()
  const rootRef = useRef<HTMLDivElement>(null)
  const dragTargets = useRef(new Set<Node>())
  const [dragging, setDragging] = useState(false)
  const localTarget = (event: DragEvent<HTMLDivElement>): Node | null =>
    enabled && event.target instanceof Node && event.currentTarget.contains(event.target) ? event.target : null
  const reset = useCallback((): void => {
    dragTargets.current.clear()
    setDragging(false)
  }, [])
  const pruneTargets = useCallback((): void => {
    for (const target of dragTargets.current) {
      if (!target.isConnected || !rootRef.current?.contains(target)) dragTargets.current.delete(target)
    }
    if (!dragTargets.current.size) setDragging(false)
  }, [])

  if ((!enabled || disabled) && dragging) setDragging(false)

  useEffect(() => {
    if (!dragging) return
    const root = rootRef.current
    const targets = dragTargets.current
    const leaveWindow = (event: globalThis.DragEvent): void => {
      if (event.relatedTarget === null && (
        event.clientX <= 0 || event.clientY <= 0
        || event.clientX >= window.innerWidth || event.clientY >= window.innerHeight
      )) reset()
    }
    const observer = new MutationObserver(pruneTargets)
    if (root) observer.observe(root, { childList: true, subtree: true })
    window.addEventListener('drop', reset, true)
    window.addEventListener('dragleave', leaveWindow)
    return () => {
      observer.disconnect()
      window.removeEventListener('drop', reset, true)
      window.removeEventListener('dragleave', leaveWindow)
      targets.clear()
    }
  }, [dragging, pruneTargets, reset])

  return <div
    ref={rootRef}
    className={mergeClasses(styles.root, className)}
    data-testid={testId}
    onDragEnter={(event: DragEvent<HTMLDivElement>) => {
      const target = localTarget(event)
      if (!target || !isFileDrag(event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      if (!disabled) {
        dragTargets.current.add(target)
        setDragging(true)
      }
    }}
    onDragOver={(event: DragEvent<HTMLDivElement>) => {
      const target = localTarget(event)
      if (!target || !isFileDrag(event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      event.dataTransfer.dropEffect = disabled ? 'none' : 'copy'
      if (!disabled) {
        dragTargets.current.add(target)
        setDragging(true)
      }
    }}
    onDragLeave={(event: DragEvent<HTMLDivElement>) => {
      const target = localTarget(event)
      if (!target || (!isFileDrag(event.dataTransfer) && !dragging)) return
      event.stopPropagation()
      dragTargets.current.delete(target)
      if (event.relatedTarget instanceof Node) {
        if (event.currentTarget.contains(event.relatedTarget)) dragTargets.current.add(event.relatedTarget)
        else reset()
      }
      pruneTargets()
    }}
    onDrop={(event: DragEvent<HTMLDivElement>) => {
      if (!localTarget(event) || !isFileDrag(event.dataTransfer)) return
      event.preventDefault()
      event.stopPropagation()
      reset()
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
