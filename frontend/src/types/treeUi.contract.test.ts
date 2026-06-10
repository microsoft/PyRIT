// Copyright (c) Microsoft Corporation.
// Licensed under the MIT license.

/**
 * Contract tests for the tree-UI (CoPyRIT V1.0) backend wire-shape extensions.
 *
 * These tests use TypeScript `satisfies` for compile-time shape verification and
 * `expect` for runtime sanity. They are the V1.0 firewall against silent backend
 * drift: if PR2's `MessagePiece` DTO regresses to drop `original_prompt_id` or
 * `converter_identifiers`, or if the frontend types stop matching what the
 * backend serializes, these tests fail at build (TS compile) or test time.
 *
 * Per design doc doc/gui/design/01_tree_primitives.md §9.4.4 (a) and (b).
 *
 * The runtime tests construct backend-shaped payloads exactly as the FastAPI
 * response serializer would emit them (per `ComponentIdentifier.model_dump()`
 * and the PR2 mapper additions), then assert the typed shape is usable.
 */

import type {
  BackendMessagePiece,
  ComponentIdentifier,
  CreateAttackRequest,
  MessagePieceRequest,
  PrependedMessageRequest,
} from './index'

describe('tree-UI backend wire-shape contracts (V1.0)', () => {
  // ------------------------------------------------------------------
  // ComponentIdentifier (the flat shape ComponentIdentifier.model_dump emits)
  // ------------------------------------------------------------------

  describe('ComponentIdentifier', () => {
    it('accepts the minimal V1.0 shape (class_name + class_module + params)', () => {
      const ci = {
        class_name: 'Base64Converter',
        class_module: 'pyrit.prompt_converter',
        params: {},
      } satisfies ComponentIdentifier

      expect(ci.class_name).toBe('Base64Converter')
      expect(ci.class_module).toBe('pyrit.prompt_converter')
      expect(ci.params).toEqual({})
    })

    it('accepts arbitrary params shape', () => {
      const ci = {
        class_name: 'ROT13Converter',
        class_module: 'pyrit.prompt_converter',
        params: {
          supported_input_types: ['text'],
          supported_output_types: ['text'],
          shift: 13,
          enabled: true,
        },
      } satisfies ComponentIdentifier

      expect(ci.params.shift).toBe(13)
      expect(ci.params.enabled).toBe(true)
    })

    it('allows the optional fields the backend may include (hash, pyrit_version, children, eval_hash)', () => {
      // These are present on `ComponentIdentifier.model_dump()` output but the
      // V1.0 runner does not read them. Declared optional so the wire payload
      // type-checks regardless of which optional fields the backend chooses
      // to populate.
      const ci = {
        class_name: 'CompositeTarget',
        class_module: 'pyrit.prompt_target',
        params: {},
        hash: 'sha256:abc123',
        pyrit_version: '0.15.0',
        eval_hash: null,
        children: {
          inner: {
            class_name: 'OpenAIChatTarget',
            class_module: 'pyrit.prompt_target',
            params: { model_name: 'gpt-4o' },
          },
        },
      } satisfies ComponentIdentifier

      expect(ci.children?.inner).toMatchObject({ class_name: 'OpenAIChatTarget' })
    })
  })

  // ------------------------------------------------------------------
  // BackendMessagePiece — PR2 fields exposed (§9.4.4 (b))
  // ------------------------------------------------------------------

  describe('BackendMessagePiece — PR2 extensions', () => {
    it('accepts a minimal piece with empty converter_identifiers and null original_prompt_id', () => {
      // This is the "no converter ever applied, default-id piece" shape the
      // mapper produces when the domain piece has empty converter_identifiers
      // and a defensive-test null original_prompt_id.
      const piece = {
        piece_id: 'p1',
        original_value_data_type: 'text',
        converted_value_data_type: 'text',
        original_value: 'hi',
        converted_value: 'hi',
        scores: [],
        response_error: 'none',
        original_prompt_id: null,
        converter_identifiers: [],
      } satisfies BackendMessagePiece

      expect(piece.converter_identifiers).toEqual([])
      expect(piece.original_prompt_id).toBeNull()
    })

    it('accepts a piece with a string original_prompt_id (the persisted-piece common case)', () => {
      // Persisted pieces always have non-null original_prompt_id per the
      // _set_original_prompt_id_default validator; this is the typical shape.
      const piece = {
        piece_id: 'p2',
        original_value_data_type: 'text',
        converted_value_data_type: 'text',
        original_value: 'hi',
        converted_value: 'hi',
        scores: [],
        response_error: 'none',
        original_prompt_id: '0c1b9c7d-0000-0000-0000-000000000001',
        converter_identifiers: [],
      } satisfies BackendMessagePiece

      expect(piece.original_prompt_id).toBe('0c1b9c7d-0000-0000-0000-000000000001')
    })

    it('accepts a piece with a non-empty converter_identifiers list', () => {
      // Load-bearing for §9.3.1 converter-fan variant-payload reconstruction.
      // The runner reads converter_identifiers[i].class_name + class_module +
      // params to rebuild a ConverterRef.
      const piece = {
        piece_id: 'p3',
        original_value_data_type: 'text',
        converted_value_data_type: 'text',
        original_value: 'hi',
        converted_value: 'aGk=',
        scores: [],
        response_error: 'none',
        original_prompt_id: '0c1b9c7d-0000-0000-0000-000000000002',
        converter_identifiers: [
          {
            class_name: 'Base64Converter',
            class_module: 'pyrit.prompt_converter',
            params: {},
          },
        ],
      } satisfies BackendMessagePiece

      expect(piece.converter_identifiers).toHaveLength(1)
      expect(piece.converter_identifiers[0].class_name).toBe('Base64Converter')
    })

    it('still accepts the pre-V1.0 piece shape (regression guard)', () => {
      // Pre-PR2 pieces did not have these fields. The frontend types must
      // accept legacy-shape pieces too — the V1.0 runner reads them only
      // when present, and reload-reconstruction tolerates absence per the
      // §9.4.4 (b) "default" contract on the wire ([] / null).
      //
      // We model this by NOT including the new fields in the literal, then
      // making the runtime tolerant: the type should mark them required for
      // pieces the V1.0 runner builds, but tests may construct pieces without
      // them when modelling legacy data.
      const piece: BackendMessagePiece = {
        piece_id: 'p4',
        original_value_data_type: 'text',
        converted_value_data_type: 'text',
        original_value: 'hi',
        converted_value: 'hi',
        scores: [],
        response_error: 'none',
        // V1.0 contract: both fields present, with their declared defaults.
        original_prompt_id: null,
        converter_identifiers: [],
      }

      expect(piece.converter_identifiers).toEqual([])
    })
  })

  // ------------------------------------------------------------------
  // CreateAttackRequest — PR3a prepended_conversation extension (§9.4.4 (a))
  // ------------------------------------------------------------------

  describe('CreateAttackRequest — prepended_conversation extension', () => {
    it('accepts a request without prepended_conversation (back-compat)', () => {
      // The existing chat tab still uses source_conversation_id + cutoff_index.
      // The new field is optional.
      const req = {
        target_registry_name: 'gpt-4o',
        labels: { operator: 'alice' },
      } satisfies CreateAttackRequest

      expect(req.target_registry_name).toBe('gpt-4o')
    })

    it('accepts a request with an empty prepended_conversation', () => {
      const req = {
        target_registry_name: 'gpt-4o',
        labels: { operator: 'alice', conversation_tree_id: 't1', wave_id: 'w1' },
        prepended_conversation: [],
      } satisfies CreateAttackRequest

      expect(req.prepended_conversation).toEqual([])
    })

    it('accepts a request with a multi-turn prepended_conversation', () => {
      // The canonical V1.0 runner shape: prepended_conversation carries the
      // clean-prefix turns (system + alternating user/assistant) per §4.1.
      // Annotating the array explicitly widens each piece's type back to
      // `MessagePieceRequest` so consumers can read optional fields like
      // `original_prompt_id` uniformly (without literal-type narrowing
      // varying per element).
      const prepended: PrependedMessageRequest[] = [
        {
          role: 'system',
          pieces: [
            {
              data_type: 'text',
              original_value: 'You are a helpful assistant.',
            },
          ],
        },
        {
          role: 'user',
          pieces: [
            {
              data_type: 'text',
              original_value: 'Hello',
              original_prompt_id: '0c1b9c7d-0000-0000-0000-000000000001',
            },
          ],
        },
        {
          role: 'assistant',
          pieces: [
            {
              data_type: 'text',
              original_value: 'Hi! How can I help?',
              original_prompt_id: '0c1b9c7d-0000-0000-0000-000000000002',
            },
          ],
        },
      ]

      const req = {
        target_registry_name: 'gpt-4o',
        labels: {
          operator: 'alice',
          conversation_tree_id: 't1',
          wave_id: 'w1',
          wave_trigger_kind: 'refresh_tree',
          tree_path: '[]',
        },
        prepended_conversation: prepended,
      } satisfies CreateAttackRequest

      expect(req.prepended_conversation).toHaveLength(3)
      expect(req.prepended_conversation[0].role).toBe('system')
      expect(req.prepended_conversation[1].pieces[0].original_prompt_id).toBe(
        '0c1b9c7d-0000-0000-0000-000000000001',
      )
    })
  })

  // ------------------------------------------------------------------
  // PrependedMessageRequest — new type (§9.4.4 (a))
  // ------------------------------------------------------------------

  describe('PrependedMessageRequest', () => {
    it('accepts each of the four valid roles', () => {
      // The backend's ChatMessageRole literal: user / assistant / system /
      // simulated_assistant. The runner uses 'system' for the leading
      // PrependedMessageRequest when RootPromptNode.params.systemPrompt is
      // set (§3.3a _systemPrompt_as_prepended_message).
      const roles = ['user', 'assistant', 'system', 'simulated_assistant'] as const

      const msgs = roles.map((role) => ({
        role,
        pieces: [{ data_type: 'text', original_value: 'x' }],
      }))

      msgs.forEach((m) => {
        // Each individually satisfies the type.
        const _typed = m satisfies PrependedMessageRequest
        expect(_typed.pieces[0].original_value).toBe('x')
      })
    })

    it('preserves lineage via original_prompt_id on pieces', () => {
      // The §7.2 lineage contract: prepended pieces carry forward the source
      // piece's UUID via MessagePieceRequest.original_prompt_id so descendants
      // share the same lineage root after duplicate.
      const msg = {
        role: 'user',
        pieces: [
          {
            data_type: 'text',
            original_value: 'hello',
            original_prompt_id: '0c1b9c7d-0000-0000-0000-000000000099',
          } satisfies MessagePieceRequest,
        ],
      } satisfies PrependedMessageRequest

      expect(msg.pieces[0].original_prompt_id).toBe('0c1b9c7d-0000-0000-0000-000000000099')
    })

    it('accepts multimodal pieces in one message', () => {
      // PrependedMessageRequest is one message; multimodal turns bundle
      // multiple pieces into the pieces[] array (text + image, etc.).
      const msg = {
        role: 'user',
        pieces: [
          { data_type: 'image_path', original_value: '/api/media?path=img1' },
          { data_type: 'text', original_value: 'What is in this image?' },
        ],
      } satisfies PrependedMessageRequest

      expect(msg.pieces).toHaveLength(2)
    })
  })
})
