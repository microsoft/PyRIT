import { type FormEvent, type RefObject, useRef, useState } from 'react'

import type { BuildRunRequestResult } from '@/components/Scenarios/scenarioLaunchRequest'
import { scenariosApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScenarioRunSummary } from '@/types'

interface UseScenarioLaunchInput {
  /** The run request built from the current form, or why it can't be built. */
  requestResult: BuildRunRequestResult
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
  clearValidationError: () => void
  /** Synchronous submit state, for guards that run before React re-renders. */
  isSubmitting: () => boolean
  launchButtonRef: RefObject<HTMLButtonElement | null>
  submitForm: (event: FormEvent<HTMLFormElement>) => void
  dismissPreview: () => void
  changePreviewOpen: (open: boolean) => void
  confirmLaunch: () => Promise<void>
}

function focusSoon(ref: RefObject<HTMLButtonElement | null>): void {
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
  submitBlocked,
  launchBlocked,
  onLaunched,
}: UseScenarioLaunchInput): ScenarioLaunch {
  const [previewOpen, setPreviewOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [validationError, setValidationError] = useState<string | null>(null)
  // Synchronous guard against a double-submit racing ahead of the state update.
  const isSubmittingRef = useRef(false)
  const launchButtonRef = useRef<HTMLButtonElement | null>(null)

  const submitForm = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    if (submitBlocked || isSubmittingRef.current) return
    setApiError(null)
    if (!requestResult.ok) {
      setValidationError(requestResult.error)
      return
    }
    setValidationError(null)
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
      setApiError(toApiError(err).detail)
      setPreviewOpen(false)
      focusSoon(launchButtonRef)
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
    clearValidationError: () => setValidationError(null),
    isSubmitting: () => isSubmittingRef.current,
    launchButtonRef,
    submitForm,
    dismissPreview,
    changePreviewOpen,
    confirmLaunch,
  }
}
