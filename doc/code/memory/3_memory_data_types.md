# 3. Memory Types

There are several types of data you can retrieve from memory at any point in time using the `MemoryInterface`.

## MessagePiece and Message

One of the most fundamental data structures in PyRIT is [MessagePiece](../../../pyrit/models/message_piece.py) and [Message](../../../pyrit/models/message.py). These classes provide the foundation for multi-modal interaction tracking throughout the framework.

### MessagePiece

`MessagePiece` represents a single piece of a request to a target. It is the atomic unit that is stored in the database and contains comprehensive metadata about each interaction.

**Key Fields:**

- **`id`**: Unique identifier for the piece (UUID)
- **`conversation_id`**: Identifier grouping pieces into a single conversation with a target
- **`sequence`**: Order of the piece within a conversation
- **`role`**: The role in the conversation (e.g., `user`, `assistant`, `system`)
- **`original_value`**: The original prompt text or file path (for images, audio, etc.)
- **`original_value_data_type`**: The data type of the original value (e.g., `text`, `image_path`, `audio_path`)
- **`converted_value`**: The prompt after any conversions/transformations have been applied
- **`converted_value_data_type`**: The data type of the converted value
- **`labels`**: Dictionary of labels for categorization and filtering
- **`prompt_metadata`**: Component-specific metadata (e.g., blob URIs, document types)
- **`converter_identifiers`**: List of converters applied to transform the prompt
- **`prompt_target_identifier`**: Information about the target that received this prompt
- **`attack_identifier`**: Information about the attack that generated this prompt
- **`scorer_identifier`**: Information about the scorer that evaluated this prompt
- **`response_error`**: Error status (e.g., `none`, `blocked`, `processing`)
- **`originator`**: Source of the prompt (`attack`, `converter`, `scorer`, `undefined`)
- **`scores`**: List of `Score` objects associated with this piece
- **`targeted_harm_categories`**: Harm categories associated with the prompt
- **`timestamp`**: When the piece was created

This rich context allows PyRIT to track the full lifecycle of each interaction, including transformations, targeting, scoring, and error handling.

### Message

`Message` represents a single request or response to a target and can contain multiple `MessagePieces`. This allows for multi-modal interactions where, for example, you send both text and an image in a single request.

**Examples:**
- A text-only message: 1 `Message` containing 1 `MessagePiece`
- An image with caption: 1 `Message` containing 2 `MessagePieces` (text + image)
- A conversation: Multiple `Messages` linked by the same `conversation_id`

**Validation Rules:**
- All `MessagePieces` in a `Message` must share the same
   -  `conversation_id`
   - `sequence` number
   - `role`
- All `MessagePieces` have a non-null `converted_value`

### Conversation Structure

A conversation is a list of `Messages` that share the same `conversation_id`. The sequence of the `MessagePieces` and their corresponding `Messages` dictates the order of the conversation.

Here is a sample conversation made up of three `Messages` which all share the same conversation ID. The first `Message` is the `system` message, followed by a multi-modal `user` prompt with a text `MessagePiece` and an image `MessagePiece`, and finally the `assistant` response in the form of a text `MessagePiece`.

```{mermaid}
flowchart
   subgraph Conversation: 001
      subgraph Message: sequence 2
         subgraph "MessagePiece: <br>sequence: 2<br>conversation_id: 001<br>role: assistant<br>value: The image shows a wave ..."
         end
      end
      subgraph Message: sequence 1
         subgraph "MessagePiece: <br>sequence: 1<br>conversation_id: 001<br>role: user<br>value: tell me what's in this image"
         end
         subgraph "MessagePiece: <br>sequence: 1<br>conversation_id: 001<br>role: user<br>value: data/wave.png"
         end
      end
      subgraph Message: sequence 0
         subgraph "MessagePiece: <br>sequence: 0<br>conversation_id: 001<br>role: system<br>value: be a helpful assistant"
         end
      end
   end
```

This architecture is plumbed throughout PyRIT, providing flexibility to interact with various modalities seamlessly. All pieces are stored in the database as individual `MessagePieces` and are reassembled when needed. The `PromptNormalizer` automatically adds these to the database as prompts are sent.

## Seeds

All seed types inherit from [`Seed`](../../../pyrit/models/seeds/seed.py), which provides common fields (`value`, `value_sha256`, `dataset_name`, `harm_categories`, `is_general_technique`, `metadata`, etc.) along with Jinja2 templating and YAML loading support.

### Seed Types

- [`SeedPrompt`](../../../pyrit/models/seeds/seed_prompt.py) — A prompt to send to a target. Adds `data_type` (text, image_path, audio_path, etc.), `role` (user/assistant), `sequence` (for multi-turn ordering), and template `parameters`. This is the most common seed type and can be translated to and from `MessagePieces`.

- [`SeedObjective`](../../../pyrit/models/seeds/seed_objective.py) — The goal of an attack (e.g., "Generate hate speech content"). Always text. Cannot be a general technique.

- [`SeedSimulatedConversation`](../../../pyrit/models/seeds/seed_simulated_conversation.py) — Configuration for dynamically generating multi-turn conversations. Specifies system prompt paths, number of turns, and sequence offsets. The actual generation happens in the executor layer.

### Seed Groups

Seeds are organized into [`SeedGroup`](../../../pyrit/models/seeds/seed_group.py) containers that enforce consistency (shared `prompt_group_id`, valid role sequences, no duplicate sequence numbers). Two specialized subclasses add further constraints:

- [`SeedAttackGroup`](../../../pyrit/models/seeds/seed_attack_group.py) — Requires exactly one `SeedObjective`. Represents a complete attack specification: an objective plus optional prompts or simulated conversation config.

- [`SeedAttackTechniqueGroup`](../../../pyrit/models/seeds/seed_attack_technique_group.py) — All seeds must have `is_general_technique=True` and no `SeedObjective` is allowed. Represents reusable attack techniques (jailbreaks, role-plays, etc.) that can be composed with any objective.


## Scores

[`Score`](../../../pyrit/models/score.py) objects represent evaluations of prompts or responses. Scores are generated by scorer components and attached to `MessagePieces` to track the success or characteristics of attacks. When a prompt is scored, it is added to the database and can be queried later.

**Key Fields:**

- **`score_value`**: The actual score (e.g., `"true"`, `"0.75"`)
- **`score_value_description`**: Human-readable description of the score
- **`score_type`**: Type of score (`true_false` or `float_scale`)
- **`score_category`**: Categories the score applies to (e.g., `["hate", "violence"]`)
- **`score_rationale`**: Explanation of why the score was assigned
- **`scorer_class_identifier`**: Information about the scorer that generated this score
- **`message_piece_id`**: The ID of the piece/response being scored
- **`task`**: The original attacker's objective being evaluated
- **`score_metadata`**: Custom metadata specific to the scorer

Scores enable automated evaluation of attack success, content harmfulness, and other metrics throughout PyRIT's red teaming workflows.

## AttackResults

[`AttackResult`](../../../pyrit/models/attack_result.py) objects encapsulate the complete outcome of an attack execution, including metrics, evidence, and success determination. When an attack is run, the AttackResult is added to the database and can be queried later.

**Key Fields:**

- **`conversation_id`**: The conversation that produced this result
- **`objective`**: Natural-language description of the attacker's goal
- **`attack_identifier`**: `ComponentIdentifier` identifying the attack strategy used
- **`atomic_attack_identifier`**: Composite `ComponentIdentifier` combining the attack technique with seed identifiers from the dataset (see [ComponentIdentifiers](#componentidentifiers) below)
- **`last_response`**: The final `MessagePiece` generated in the attack
- **`last_score`**: The final score assigned to the last response
- **`executed_turns`**: Number of turns executed in the attack
- **`execution_time_ms`**: Total execution time in milliseconds
- **`outcome`**: The attack outcome (`SUCCESS`, `FAILURE`, or `UNDETERMINED`)
- **`outcome_reason`**: Optional explanation for the outcome
- **`related_conversations`**: Set of related conversation references
- **`metadata`**: Arbitrary metadata about the attack execution

`AttackResult` objects provide comprehensive reporting on attack campaigns, enabling analysis of red teaming effectiveness and vulnerability identification.

## ComponentIdentifiers

[`ComponentIdentifier`](../../../pyrit/identifiers/component_identifier.py) is an immutable snapshot of a component's behavioral configuration. A single type is used for all components — targets, scorers, converters, and attacks — enabling uniform storage and composition.

**Key Fields:**

- **`class_name`** / **`class_module`**: The Python class and module of the component
- **`params`**: Behavioral parameters (e.g., `temperature`, `model_name`)
- **`children`**: Named child identifiers for composition (e.g., a scorer's `prompt_target`)
- **`hash`**: Content-addressed SHA256 hash computed from class, params, and children

Identifiers are content-addressed: the same configuration always produces the same hash, and any change to params or children produces a different one. This is used throughout PyRIT to track which exact configuration produced a given result.

### Composite Identifiers

For atomic attacks, `build_atomic_attack_identifier` composes a tree of identifiers:

- **`attack_technique`** — the attack strategy and its children (target, converters, scorer, technique seeds)
- **`seed_identifiers`** — all seeds from the seed group, for traceability

### Eval Hashing

[`EvaluationIdentifier`](../../../pyrit/identifiers/evaluation_identifier.py) subclasses wrap a `ComponentIdentifier` and compute a separate **eval hash** that strips operational params (like endpoint URLs) so the same logical configuration on different deployments produces the same hash. This enables grouping equivalent runs for evaluation comparison.
