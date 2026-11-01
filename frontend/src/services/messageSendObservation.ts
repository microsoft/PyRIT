import { attacksApi } from '@/services/api'
import type { AttackSummary, MessageSendConversation, MessageSendObservation, MessageSendStatus } from '@/types'

function finished(progress: MessageSendStatus): boolean {
  return ['completed', 'failed', 'interrupted'].includes(progress.state)
}

function confirmed(progress: MessageSendStatus): boolean {
  return (progress.conversations ?? [progress]).some((conversation: MessageSendConversation) => (
    conversation.request_turn_number != null && conversation.failure_stage !== 'preparation'
  ))
}

/** Share account-owned receipt reads with the abortable chat view. */
export function observeMessageSend(
  attackId: string,
  acceptance: Promise<MessageSendStatus>,
  onConfirmed: (progress: MessageSendStatus, attack: AttackSummary) => void,
  onError: (cause: unknown) => void,
): MessageSendObservation {
  let latest: MessageSendStatus | undefined
  let progressFailure: { cause: unknown } | undefined
  let progressRevision = 0
  let deliveredRevision = 0
  let remembered = false
  let pending: Promise<MessageSendStatus> | undefined = acceptance.then((progress: MessageSendStatus) => {
    latest = progress
    return progress
  }).catch((cause: unknown) => {
    progressFailure = { cause }
    throw cause
  }).finally(() => { pending = undefined })
  let attackRead: Promise<AttackSummary> | undefined
  let attackRevision = -1
  let attackReadSucceeded = false
  const pollProgress = (): Promise<MessageSendStatus> => {
    if (pending) return pending
    if (progressFailure) return Promise.reject(progressFailure.cause)
    if (!latest) return Promise.reject(new Error('The send has no confirmed acceptance.'))
    if (finished(latest)) return Promise.resolve(latest)
    pending = attacksApi.getMessageSend(attackId, latest.send_id).then((progress: MessageSendStatus) => {
      latest = progress
      progressRevision += 1
      return progress
    }).catch((cause: unknown) => {
      progressFailure = { cause }
      throw cause
    }).finally(() => { pending = undefined })
    return pending
  }
  const observation: MessageSendObservation = {
    readProgress: (refresh: boolean = false): Promise<MessageSendStatus> => {
      if (refresh && latest) progressFailure = undefined
      if (progressFailure) return Promise.reject(progressFailure.cause)
      // A view must see a completed sibling even if the owner is already polling the next snapshot.
      if (latest && progressRevision > deliveredRevision) {
        deliveredRevision = progressRevision
        return Promise.resolve(latest)
      }
      return pollProgress().then((progress: MessageSendStatus) => {
        deliveredRevision = progressRevision
        return progress
      })
    },
    readAttack: (refresh: boolean = false): Promise<AttackSummary> => {
      if (refresh || (attackReadSucceeded && attackRevision < progressRevision)) attackRead = undefined
      if (!attackRead) {
        attackReadSucceeded = false
        attackRevision = progressRevision
      }
      attackRead ??= attacksApi.getAttack(attackId).then((attack: AttackSummary) => {
        attackReadSucceeded = true
        if (!remembered && latest && confirmed(latest)) {
          onConfirmed(latest, attack)
          remembered = true
        }
        return attack
      })
      return attackRead
    },
  }
  const confirm = async (): Promise<void> => {
    let progress = await pollProgress()
    while (!finished(progress)) progress = await pollProgress()
    if (confirmed(progress)) await observation.readAttack()
  }
  void confirm().catch(onError)
  return observation
}
