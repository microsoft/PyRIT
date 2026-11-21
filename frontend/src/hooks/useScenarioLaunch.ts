import { type FormEvent, type RefObject, useRef, useState } from 'react'

import type { BuildRunRequestResult } from '@/components/Scenarios/scenarioLaunchRequest'
import { scenariosApi } from '@/services/api'
import { apiErrorMessage, toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'

interface UseScenarioLaunchInput {
  /** The run request built from the current form, or why it can't be built. */
  requestResult: BuildRunRequestResult
  /**
   * The library's message for fewer datasets than the scenario's declared minimum, or null when there are
   * enough. Launch scan shows it on the dataset picker instead of opening the confirmation.
   */
  datasetSelectionMessage: string | null
  /** True when the form can't open the confirmation, for example while a conversation is being edited. */
  submitBlocked: boolean
  /** True when a confirmed launch must not start, for example while the runtime is not ready. */
  launchBlocked: boolean
  onLaunched: (summary: ScenarioRunSummary) => void
}

export interface ScenarioLaunch {
  previewOpen: boolean
  submitting: boolean
  apiError: string | null
  validationError: string | null
  /** The dataset picker's message after Launch scan with too few datasets; it clears once there are enough. */
  datasetSelectionError: string | null
  clearValidationError: () => void
  /** Synchronous submit state, for guards that run before React re-renders. */
  isSubmitting: () => boolean
  launchButtonRef: RefObject<HTMLButtonElement | null>
  /** The banner for a rejected launch; it takes focus so the reason scrolls into view. */
  apiErrorRef: RefObject<HTMLDivElement | null>
  /** The dataset picker; it takes focus when it reports too few datasets. */
  datasetPickerRef: RefObject<HTMLInputElement | null>
  submitForm: (event: FormEvent<HTMLFormElement>) => void
  dismissPreview: () => void
  changePreviewOpen: (open: boolean) => void
  confirmLaunch: () => Promise<void>
}

function focusSoon(ref: RefObject<HTMLElement | null>): void {
  const focus = () => {
    ref.current?.focus()
  }
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(focus)
  } else {
    setTimeout(focus, 0)
  }
}

/** Own the confirmation dialog and the single in-flight launch for a scenario run. */
export function useScenarioLaunch({
  requestResult,
  datasetSelectionMessage,
  submitBlocked,
  launchBlocked,
  onLaunched,
}: UseScenarioLaunchInput): ScenarioLaunch {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  const [showDatasetSelectionError, setShowDatasetSelectionError] = useState(false)
  // Synchronous guard against a double-submit racing ahead of the state update.
  const isSubmittingRef = useRef(false)
  const launchButtonRef = useRef<HTMLButtonElement | null>(null)
  const apiErrorRef = useRef<HTMLDivElement | null>(null)
  const datasetPickerRef = useRef<HTMLInputElement | null>(null)

  // Enough datasets clear the message, so a later shortfall waits for the next Launch scan.
  if (showDatasetSelectionError && datasetSelectionMessage === null) {
    setShowDatasetSelectionError(false)
  }

  const submitForm = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (submitBlocked || isSubmittingRef.current) return
    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      return
    }
    setValidationError(null)
    if (datasetSelectionMessage !== null) {
      setShowDatasetSelectionError(true)
      // Focus scrolls the form to the picker; scrollIntoView would also scroll the app shell.
      focusSoon(datasetPickerRef)
      return
    }
    setPreviewOpen(true)
  }

  const dismissPreview = (): void => {
    if (!submitting) {
      setPreviewOpen(false)
      focusSoon(launchButtonRef)
    }
  }

  const changePreviewOpen = (open: boolean): void => {
    if (!submitting) {
      setPreviewOpen(open)
      if (!open) {
        focusSoon(launchButtonRef)
      }
    }
  }

  const confirmLaunch = async (): Promise<void> => {
    if (isSubmittingRef.current || launchBlocked) {
      return
    }

    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      setPreviewOpen(false)
      return
    }

    isSubmittingRef.current = true
    setSubmitting(true)
    setValidationError(null)

    try {
      const summary = await scenariosApi.startRun(requestResult.request)
      setPreviewOpen(false)
      onLaunched(summary)
    } catch (err) {
      setApiError(apiErrorMessage(toApiError(err)))
      setPreviewOpen(false)
      focusSoon(apiErrorRef)
    } finally {
      isSubmittingRef.current = false
      setSubmitting(false)
    }
  }

  return {
    previewOpen,
    submitting,
    apiError,
    validationError,
    datasetSelectionError: showDatasetSelectionError ? datasetSelectionMessage : null,
    clearValidationError: () => setValidationError(null),
    isSubmitting: () => isSubmittingRef.current,
    launchButtonRef,
    apiErrorRef,
    datasetPickerRef,
    submitForm,
    dismissPreview,
    changePreviewOpen,
    confirmLaunch,
  }
}
