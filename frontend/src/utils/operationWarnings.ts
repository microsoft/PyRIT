import type { OperationSelectionState } from '@/hooks/useOperationSelection'

export function getOperationWarning(selection: OperationSelectionState): string | undefined {
  switch (selection.status) {
    case 'empty':
      return 'Set operation in the bar. No operation is selected.'
    case 'unregistered':
      return 'Choose a saved Operation, register, or clear this one to start new attacks and scans.'
    case 'missing':
      return 'The saved operation was not found. Select another operation or clear it to start new attacks and scans.'
    case 'error':
      return `Could not load the selected operation: ${selection.error}. Retry or clear it in the bar to start new attacks and scans.`
    default:
      return undefined
  }
}
