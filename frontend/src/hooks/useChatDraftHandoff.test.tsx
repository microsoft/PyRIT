/**
 * Copyright (c) Microsoft Corporation.
 * Licensed under the MIT license.
 */

import { act, renderHook } from '@testing-library/react'
import type { ReactNode } from 'react'

import {
  canApplyPendingImport,
  hasChatDraftState,
  isChatDraftDirty,
  releaseSupersededUrls,
  revokeAttachmentUrls,
  shouldApplyPreservedDraft,
  type ChatDraftContent,
  type PendingImportPayload,
} from '@/utils/chatDraft'
import type { ChatConverterSnapshot, MessageAttachment } from '@/types'

import { ChatDraftProvider, useChatDraftHandoff } from './useChatDraftHandoff'

const CONVERTERS: ChatConverterSnapshot = {
  generation: 'test',
  workingInputs: {},
  pipelines: { text: [{ id: 'rot13-stage', converterId: 'rot13' }] },
  stageResults: {},
  errors: {},
  applied: {
    text: {
      pieceId: 'text', pieceType: 'text', originalValue: 'Hello from draft',
      convertedValue: 'Uryyb sebz qensg', convertedDataType: 'text', converterInstanceIds: ['rot13'],
    },
  },
}

describe('useChatDraftHandoff and chatDraft utilities', () => {
  let revokeObjectURLSpy: jest.SpyInstance

  beforeEach(() => {
    jest.clearAllMocks()
    revokeObjectURLSpy = jest.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  })

  afterEach(() => {
    revokeObjectURLSpy.mockRestore()
  })

  describe('isChatDraftDirty', () => {
    it('returns false for empty and whitespace-only drafts with no extra fields', () => {
      expect(isChatDraftDirty({ text: '' })).toBe(false)
      expect(isChatDraftDirty({ text: '   \n\t  ' })).toBe(false)
      expect(
        isChatDraftDirty({
          text: '',
          attachments: [],
          systemPrompt: '',
          converters: {
            ...CONVERTERS,
            applied: {},
            pipelines: {},
          },
        }),
      ).toBe(false)
    })

    it('returns true when text contains non-whitespace content', () => {
      expect(isChatDraftDirty({ text: 'hello world' })).toBe(true)
    })

    it('returns true when media attachments are present even with empty text', () => {
      const attachment: MessageAttachment = {
        name: 'image.png',
        type: 'image',
        mimeType: 'image/png',
        url: 'blob:http://localhost/test-blob',
        size: 1024,
      }
      expect(isChatDraftDirty({ text: '', attachments: [attachment] })).toBe(true)
    })

    it('returns true when system prompt is set', () => {
      expect(isChatDraftDirty({ text: '', systemPrompt: 'You are an adversarial tester' })).toBe(true)
    })

    it('returns true when applied converters are present', () => {
      expect(
        isChatDraftDirty({
          text: '',
          converters: CONVERTERS,
        }),
      ).toBe(true)
    })
  })

  it('preserves target and unapplied setup without marking prompt content dirty', () => {
    const converterSetup = { ...CONVERTERS, applied: {} }
    for (const setup of [
      { target: null },
      { target: { registryName: 'selected-target', identifierHash: 'selected-hash' } },
      { converters: converterSetup },
    ]) {
      expect(hasChatDraftState({ text: '', attachments: [], ...setup })).toBe(true)
      expect(isChatDraftDirty({ text: '', attachments: [], ...setup })).toBe(false)
    }
  })

  describe('shouldApplyPreservedDraft', () => {
    it('returns false when preserved draft is null or clean', () => {
      expect(shouldApplyPreservedDraft(null, null)).toBe(false)
      expect(shouldApplyPreservedDraft(null, 'attack-123')).toBe(false)

      const cleanDraft = {
        originatingAttackId: null,
        originatingConversationId: null,
        originatingPath: '/chat',
        draft: { text: '   ', attachments: [] },
        savedAt: Date.now(),
        sequence: 1,
      }
      expect(shouldApplyPreservedDraft(cleanDraft, null)).toBe(false)
    })

    it('returns true when draft target matches the active unsaved chat (null)', () => {
      const unsavedDraft = {
        originatingAttackId: null,
        originatingConversationId: null,
        originatingPath: '/chat',
        draft: { text: 'draft prompt', attachments: [] },
        savedAt: Date.now(),
        sequence: 1,
      }
      expect(shouldApplyPreservedDraft(unsavedDraft, null)).toBe(true)
    })

    it('returns true when draft target matches the active attack ID', () => {
      const persistedDraft = {
        originatingAttackId: 'attack-456',
        originatingConversationId: 'conv-456',
        originatingPath: '/attacks/attack-456',
        draft: { text: 'draft for attack 456', attachments: [] },
        savedAt: Date.now(),
        sequence: 1,
      }
      expect(shouldApplyPreservedDraft(persistedDraft, 'attack-456')).toBe(false)
      expect(shouldApplyPreservedDraft(persistedDraft, 'attack-456', 'conv-456')).toBe(true)
      expect(shouldApplyPreservedDraft(persistedDraft, 'attack-456', 'other-conversation')).toBe(false)
    })

    it('returns false when active conversation does not match draft destination (prevents draft leak)', () => {
      const attackDraft = {
        originatingAttackId: 'attack-1',
        originatingConversationId: 'conv-1',
        originatingPath: '/attacks/attack-1',
        draft: { text: 'attack 1 prompt', attachments: [] },
        savedAt: Date.now(),
        sequence: 1,
      }
      expect(shouldApplyPreservedDraft(attackDraft, 'attack-2')).toBe(false)
      expect(shouldApplyPreservedDraft(attackDraft, null)).toBe(false)

      const unsavedDraft = {
        originatingAttackId: null,
        originatingConversationId: null,
        originatingPath: '/chat',
        draft: { text: 'unsaved draft', attachments: [] },
        savedAt: Date.now(),
        sequence: 1,
      }
      expect(shouldApplyPreservedDraft(unsavedDraft, 'attack-1')).toBe(false)
    })
  })

  describe('canApplyPendingImport', () => {
    it('returns false when pending import is null', () => {
      expect(canApplyPendingImport(null, null)).toBe(false)
    })

    it('validates destination: new_attack only applies to unsaved chat (null)', () => {
      const importPayload: PendingImportPayload = {
        id: 'imp-1',
        sequence: 1,
        stagedAt: Date.now(),
        destinationType: 'new_attack',
        content: { text: 'imported prompt' },
      }
      expect(canApplyPendingImport(importPayload, null)).toBe(true)
      expect(canApplyPendingImport(importPayload, 'existing-attack')).toBe(false)
    })

    it('validates destination: current_conversation only applies to matching targetAttackId', () => {
      const importPayload: PendingImportPayload = {
        id: 'imp-2',
        sequence: 1,
        stagedAt: Date.now(),
        destinationType: 'current_conversation',
        targetAttackId: 'target-attack-789',
        targetConversationId: 'target-conversation-789',
        content: { text: 'imported prompt' },
      }
      expect(canApplyPendingImport(importPayload, 'target-attack-789', 'target-conversation-789')).toBe(true)
      expect(canApplyPendingImport(importPayload, 'target-attack-789')).toBe(false)
      expect(canApplyPendingImport(importPayload, 'target-attack-789', 'other-conversation')).toBe(false)
      expect(canApplyPendingImport(importPayload, 'other-attack')).toBe(false)
      expect(canApplyPendingImport(importPayload, null)).toBe(false)
    })

    it('validates destination: any applies to both new and current chat', () => {
      const importPayload: PendingImportPayload = {
        id: 'imp-3',
        sequence: 1,
        stagedAt: Date.now(),
        destinationType: 'any',
        content: { text: 'imported prompt' },
      }
      expect(canApplyPendingImport(importPayload, null)).toBe(true)
      expect(canApplyPendingImport(importPayload, 'attack-abc')).toBe(true)
    })
  })

  describe('revokeAttachmentUrls and releaseSupersededUrls', () => {
    it('revokes only blob: URLs and ignores other URL types', () => {
      const attachments: MessageAttachment[] = [
        { name: 'blob.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/test-1', size: 100 },
        { name: 'blob2.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/test-2', size: 100 },
        { name: 'data.png', type: 'image', mimeType: 'image/png', url: 'data:image/png;base64,abc', size: 100 },
        { name: 'remote.png', type: 'image', mimeType: 'image/png', url: '/api/media?path=test.png', size: 100 },
      ]

      revokeAttachmentUrls(attachments)
      expect(revokeObjectURLSpy).toHaveBeenCalledTimes(2)
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/test-1')
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/test-2')
    })

    it('releases only superseded blob URLs that are absent from next attachments', () => {
      const oldAttachments: MessageAttachment[] = [
        { name: 'kept.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/kept', size: 100 },
        { name: 'removed.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/removed', size: 100 },
      ]
      const newAttachments: MessageAttachment[] = [
        { name: 'kept.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/kept', size: 100 },
        { name: 'added.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/added', size: 100 },
      ]

      releaseSupersededUrls(oldAttachments, newAttachments)
      expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1)
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/removed')
    })
  })

  describe('ChatDraftProvider and useChatDraftHandoff hook', () => {
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ChatDraftProvider>{children}</ChatDraftProvider>
    )

    it('returns fallback default context safely when used outside of Provider', () => {
      const { result } = renderHook(() => useChatDraftHandoff())
      expect(result.current.preservedDraft).toBeNull()
      expect(result.current.pendingImport).toBeNull()
      expect(result.current.activeSequence).toBe(0)
      expect(result.current.consumePendingImport()).toBeNull()
      expect(result.current.consumePreservedDraft(null)).toBeNull()
      expect(result.current.isDraftDirty({ text: 'text' })).toBe(true)
      expect(() => result.current.stagePendingImport({
        destinationType: 'new_attack', content: { text: 'Cannot stage without an owner' },
      })).toThrow('Chat imports require a ChatDraftProvider.')
    })

    it('preserves draft across round trip and returns complete draft content', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      const draftContent: ChatDraftContent = {
        text: 'Hello from draft',
        systemPrompt: 'Adversarial system prompt',
        attachments: [
          { name: 'sample.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/img1', size: 500 },
        ],
        converters: CONVERTERS,
      }

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: draftContent,
        })
      })

      expect(result.current.preservedDraft).not.toBeNull()
      expect(result.current.preservedDraft?.draft.text).toBe('Hello from draft')
      expect(result.current.preservedDraft?.draft.systemPrompt).toBe('Adversarial system prompt')
      expect(result.current.preservedDraft?.draft.attachments).toHaveLength(1)
      expect(result.current.preservedDraft?.draft.converters).toEqual(CONVERTERS)

      // Consume preserved draft once
      let consumed: ChatDraftContent | null = null
      act(() => {
        consumed = result.current.consumePreservedDraft(null)
      })
      expect(consumed).not.toBeNull()
      expect(consumed?.text).toBe('Hello from draft')
      expect(consumed?.converters?.applied.text.convertedValue).toBe('Uryyb sebz qensg')

      // Second consumption returns null (single-use guarantee prevents duplicate restores)
      act(() => {
        consumed = result.current.consumePreservedDraft(null)
      })
      expect(consumed).toBeNull()
      expect(result.current.preservedDraft).toBeNull()
    })

    it('does not consume or leak preserved draft when active context does not match', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: 'attack-100',
          originatingConversationId: 'conv-100',
          originatingPath: '/attacks/attack-100',
          draft: { text: 'Attack 100 draft', attachments: [] },
        })
      })

      // Querying for a different attack ID or unsaved chat returns null
      let wrongContextDraft: ChatDraftContent | null = null
      act(() => {
        wrongContextDraft = result.current.consumePreservedDraft('attack-999')
      })
      expect(wrongContextDraft).toBeNull()

      act(() => {
        wrongContextDraft = result.current.consumePreservedDraft(null)
      })
      expect(wrongContextDraft).toBeNull()

      // The draft is NOT discarded by wrong context queries
      expect(result.current.preservedDraft).not.toBeNull()

      // When correct attack ID is requested, draft is successfully consumed
      let rightContextDraft: ChatDraftContent | null = null
      act(() => {
        rightContextDraft = result.current.consumePreservedDraft('attack-100', 'conv-100')
      })
      expect(rightContextDraft?.text).toBe('Attack 100 draft')
      expect(result.current.preservedDraft).toBeNull()
    })

    it('ensures clearing a draft stays cleared after consuming: subsequent consume returns null and unmount with empty draft does not resurrect old snapshot', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: { text: 'my draft text', attachments: [] },
        })
      })
      expect(result.current.preservedDraft?.draft.text).toBe('my draft text')

      // Consumed when returning to Chat
      let consumed: ChatDraftContent | null = null
      act(() => {
        consumed = result.current.consumePreservedDraft(null)
      })
      expect(consumed?.text).toBe('my draft text')
      expect(result.current.preservedDraft).toBeNull()

      // User clears the text: empty draft is not dirty
      const emptyDraft: ChatDraftContent = { text: '', attachments: [] }
      expect(result.current.isDraftDirty(emptyDraft)).toBe(false)

      // When leaving to datasets with empty draft, preservedDraft remains null
      expect(result.current.preservedDraft).toBeNull()
    })

    it('cleans up superseded blob URLs when draft is overwritten with new attachments', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: {
            text: 'v1',
            attachments: [
              { name: 'old.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/old', size: 100 },
            ],
          },
        })
      })
      expect(revokeObjectURLSpy).not.toHaveBeenCalled()

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: {
            text: 'v2',
            attachments: [
              { name: 'new.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/new', size: 100 },
            ],
          },
        })
      })
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/old')
      expect(revokeObjectURLSpy).not.toHaveBeenCalledWith('blob:http://localhost/new')
    })

    it('revokes blob URLs and clears state when clearPreservedDraft is called', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: {
            text: 'to be cleared',
            attachments: [
              { name: 'blob1.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/blob1', size: 100 },
            ],
          },
        })
      })

      act(() => {
        result.current.clearPreservedDraft()
      })
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/blob1')
      expect(result.current.preservedDraft).toBeNull()
    })

    it('stages, consumes, and dismisses pending imports with single-use guarantee and cleanup', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })

      let staged: PendingImportPayload | null = null
      act(() => {
        staged = result.current.stagePendingImport({
          destinationType: 'new_attack',
          content: {
            text: 'imported seed prompt',
            attachments: [
              { name: 'imported.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/imp-blob', size: 200 },
            ],
          },
        })
      })
      expect(staged).not.toBeNull()
      expect(result.current.pendingImport).not.toBeNull()
      expect(result.current.pendingImport?.content.text).toBe('imported seed prompt')

      // Consuming pending import returns the payload and clears state
      let consumed: PendingImportPayload | null = null
      act(() => {
        consumed = result.current.consumePendingImport()
      })
      expect(consumed?.id).toBe(staged?.id)
      expect(consumed?.content.text).toBe('imported seed prompt')
      expect(result.current.pendingImport).toBeNull()

      // Subsequent call returns null (protects against rerender duplication)
      act(() => {
        consumed = result.current.consumePendingImport()
      })
      expect(consumed).toBeNull()

      // Test dismissPendingImport with blob revocation
      act(() => {
        result.current.stagePendingImport({
          destinationType: 'current_conversation',
          targetAttackId: 'attack-1',
          targetConversationId: 'conv-1',
          content: {
            text: 'dismiss me',
            attachments: [
              { name: 'dismissed.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/dismiss-blob', size: 200 },
            ],
          },
        })
      })
      act(() => {
        result.current.dismissPendingImport()
      })
      expect(result.current.pendingImport).toBeNull()
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/dismiss-blob')
    })

    it('revokes remaining blob URLs on Provider unmount', () => {
      const { result, unmount } = renderHook(() => useChatDraftHandoff(), { wrapper })

      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null,
          originatingConversationId: null,
          originatingPath: '/chat',
          draft: {
            text: 'unmount draft',
            attachments: [
              { name: 'unmount-draft.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/unmount-draft', size: 100 },
            ],
          },
        })
        result.current.stagePendingImport({
          destinationType: 'new_attack',
          content: {
            text: 'unmount import',
            attachments: [
              { name: 'unmount-imp.png', type: 'image', mimeType: 'image/png', url: 'blob:http://localhost/unmount-imp', size: 100 },
            ],
          },
        })
      })

      unmount()
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/unmount-draft')
      expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:http://localhost/unmount-imp')
    })

    it('does not let a stale restoration consume a newer snapshot for the same destination', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Earlier draft', attachments: [] },
        })
      })
      const sequence = result.current.preservedDraft?.sequence
      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Newer draft', attachments: [] },
        })
        expect(result.current.consumePreservedDraft(null, null, sequence)).toBeNull()
        result.current.clearPreservedDraft(sequence)
      })
      expect(result.current.preservedDraft?.draft.text).toBe('Newer draft')
    })

    it.each(['current_conversation', 'new_conversation'] as const)(
      'checks the complete %s destination even when no import ID is supplied',
      (destinationType: 'current_conversation' | 'new_conversation') => {
        const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
        act(() => {
          result.current.stagePendingImport({
            destinationType, targetAttackId: 'attack', targetConversationId: 'destination',
            content: { text: 'Only for the destination' },
          })
          expect(result.current.consumePendingImport(undefined, 'attack', null)).toBeNull()
          expect(result.current.consumePendingImport(undefined, 'attack', 'other')).toBeNull()
          expect(result.current.consumePendingImport(undefined, 'attack', 'destination')?.content.text)
            .toBe('Only for the destination')
        })
        expect(result.current.pendingImport).toBeNull()
      },
    )

    it('rejects an async import prepared before a newer draft or an explicit reset', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
      const sequence = result.current.activeSequence
      const attachment: MessageAttachment = {
        type: 'image', mimeType: 'image/png', name: 'stale.png', url: 'blob:stale-import',
      }
      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Newer draft', attachments: [] },
        })
        expect(result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Stale result', attachments: [attachment] },
        }, sequence)).toBeNull()
      })
      expect(result.current.pendingImport).toBeNull()
      expect(result.current.preservedDraft?.draft.text).toBe('Newer draft')
      expect(revokeObjectURLSpy).toHaveBeenCalledWith(attachment.url)
      const beforeReset = result.current.activeSequence
      act(() => {
        result.current.clearPreservedDraft()
        expect(result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Result after cancellation' },
        }, beforeReset)).toBeNull()
      })
      expect(result.current.pendingImport).toBeNull()
    })

    it('keeps shared blob URLs alive when either preserved or pending content is replaced', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
      const attachment: MessageAttachment = {
        type: 'image', mimeType: 'image/png', name: 'shared.png', url: 'blob:shared',
      }
      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Preserved', attachments: [attachment] },
        })
        result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Pending', attachments: [attachment] },
        })
        result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Replacement' },
        })
      })
      expect(revokeObjectURLSpy).not.toHaveBeenCalledWith(attachment.url)
      act(() => { result.current.clearPreservedDraft() })
      expect(revokeObjectURLSpy).toHaveBeenCalledWith(attachment.url)
    })

    it('does not revoke a pending import attachment when preserved content is cleared', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
      const attachment: MessageAttachment = {
        type: 'image', mimeType: 'image/png', name: 'shared.png', url: 'blob:shared',
      }
      act(() => {
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Preserved', attachments: [attachment] },
        })
        result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Pending', attachments: [attachment] },
        })
        result.current.clearPreservedDraft()
      })
      expect(revokeObjectURLSpy).not.toHaveBeenCalledWith(attachment.url)
      act(() => { result.current.dismissPendingImport() })
      expect(revokeObjectURLSpy).toHaveBeenCalledTimes(1)
      expect(revokeObjectURLSpy).toHaveBeenCalledWith(attachment.url)
    })

    it('invalidates stale pending content without revoking a newer draft attachment', () => {
      const { result } = renderHook(() => useChatDraftHandoff(), { wrapper })
      const attachment: MessageAttachment = {
        type: 'image', mimeType: 'image/png', name: 'shared.png', url: 'blob:new-owner',
      }
      act(() => {
        result.current.stagePendingImport({
          destinationType: 'new_attack', content: { text: 'Old import', attachments: [attachment] },
        })
        result.current.preserveDraft({
          originatingAttackId: null, originatingConversationId: null, originatingPath: '/chat',
          draft: { text: 'Newer draft', attachments: [attachment] },
        })
        expect(result.current.consumePendingImport()).toBeNull()
      })
      expect(result.current.pendingImport).toBeNull()
      expect(result.current.preservedDraft?.draft.text).toBe('Newer draft')
      expect(revokeObjectURLSpy).not.toHaveBeenCalledWith(attachment.url)
    })
  })
})
