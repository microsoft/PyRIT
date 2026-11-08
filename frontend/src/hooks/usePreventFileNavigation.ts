import { useEffect } from 'react'

import { isFileDrag } from '@/utils/fileDrag'

/** Leave handled drops alone, but do not let an unhandled file replace the app. */
export function usePreventFileNavigation(): void {
  useEffect(() => {
    const preventNavigation = (event: DragEvent): void => {
      if (event.defaultPrevented || !isFileDrag(event.dataTransfer)) return
      event.preventDefault()
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
    }
    window.addEventListener('dragover', preventNavigation)
    window.addEventListener('drop', preventNavigation)
    return () => {
      window.removeEventListener('dragover', preventNavigation)
      window.removeEventListener('drop', preventNavigation)
    }
  }, [])
}
