import { useCallback, useEffect, useRef, useState } from 'react'

import { attacksApi } from '@/services/api'
import type { AddMessageResponse, ConversationSaveInput, SaveConversationRequest } from '@/types'
import { generateClientId } from '@/utils/clientId'
import { serializeDraft } from '@/utils/conversationDraft'

export function useConversationSave() {
  const [saving, setSaving] = useState(false)
  const pending = useRef(false)
  const mounted = useRef(true)
  const attempt = useRef<{ signature: string; id: string } | null>(null)
  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  const save = useCallback(async (
    input: ConversationSaveInput,
    destination: SaveConversationRequest['destination'],
  ): Promise<AddMessageResponse> => {
    if (pending.current) throw new Error('A conversation save is already in progress.')
    pending.current = true
    setSaving(true)
    try {
      const objective = input.objective.trim()
      const updatesObjective = destination === 'same_attack' && objective !== input.initialObjective
      const payload = {
        destination,
        attack_result_id: destination === 'same_attack' ? input.sourceAttackId ?? undefined : undefined,
        source_attack_result_id: input.sourceConversationId ? input.sourceAttackId ?? undefined : undefined,
        source_conversation_id: input.sourceConversationId ?? undefined,
        expected_objective: updatesObjective ? input.initialObjective : undefined,
        objective: destination === 'new_attack' || updatesObjective ? objective : undefined,
        target_registry_name: input.target?.target_registry_name,
        operator: input.labels?.operator,
        operation: input.labels?.operation,
        labels: input.labels,
        messages: await serializeDraft(input.messages),
      }
      const signature = JSON.stringify(payload)
      if (attempt.current?.signature !== signature) attempt.current = { signature, id: generateClientId() }
      const response = await attacksApi.saveConversation({ ...payload, save_id: attempt.current.id })
      attempt.current = null
      return response
    } finally {
      pending.current = false
      if (mounted.current) setSaving(false)
    }
  }, [])

  return { save, saving }
}
