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

## SeedPrompts

[`SeedPrompt`](../../../pyrit/models/seeds/seed_prompt.py) objects represent the starting points of conversations. They are used to assemble and initiate attacks, and can be translated to and from `MessagePieces`.

**Key Fields:**

- **`value`**: The actual prompt text or file path
- **`data_type`**: Type of data (e.g., `text`, `image_path`, `audio_path`)
- **`name`**: Name of the prompt
- **`dataset_name`**: Name of the dataset this prompt belongs to
- **`harm_categories`**: Categories of harm associated with this prompt
- **`description`**: Description of the prompt's purpose or content
- **`parameters`**: Template parameters that can be filled in dynamically
- **`prompt_group_id`**: Groups related prompts together
- **`role`**: Role in conversation (e.g., `user`, `assistant`)
- **`metadata`**: Arbitrary metadata that can be attached

`SeedPrompts` support Jinja2 templating, allowing dynamic prompt generation with parameter substitution. They can be loaded from YAML files and organized into datasets and groups for systematic testing.

## SeedObjectives

[`SeedObjective`](../../../pyrit/models/seeds/seed_objective.py) objects represent the goal or objective of an attack or test scenario. They describe what the attacker is trying to achieve and are used alongside `SeedPrompts` to define complete attack scenarios.

**Key Fields:**

- **`value`**: The objective statement describing the goal (e.g., "Generate hate speech content")
- **`data_type`**: Always `text` for objectives
- **`name`**: Name identifying the objective
- **`dataset_name`**: Name of the dataset this objective belongs to
- **`harm_categories`**: Categories of harm the objective relates to
- **`authors`**: Attribution information for the objective
- **`groups`**: Group affiliations (e.g., "AI Red Team")
- **`source`**: Source or reference for the objective
- **`metadata`**: Additional metadata about the objective

`SeedObjectives` support Jinja2 templating for dynamic objective generation and can be loaded from YAML files alongside prompts, making it easy to organize and reuse test objectives across different scenarios.

**Relationship to SeedGroups:**

`SeedObjective` and `SeedPrompt` objects are combined into [`SeedGroup`](../../../pyrit/models/seeds/seed_group.py) objects, which represent a complete test case with optional seed prompts and an objective. A SeedGroup can contain:

- Multiple prompts (for multi-turn conversations)
- A single objective (what the attack is trying to achieve)
- Both prompts and an objective (complete attack specification)


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
- **`attack_identifier`**: Information identifying the attack strategy used
- **`atomic_attack_identifier`**: Composite identifier combining the attack strategy with general technique seed identifiers from the dataset
- **`last_response`**: The final `MessagePiece` generated in the attack
- **`last_score`**: The final score assigned to the last response
- **`executed_turns`**: Number of turns executed in the attack
- **`execution_time_ms`**: Total execution time in milliseconds
- **`outcome`**: The attack outcome (`SUCCESS`, `FAILURE`, or `UNDETERMINED`)
- **`outcome_reason`**: Optional explanation for the outcome
- **`related_conversations`**: Set of related conversation references
- **`metadata`**: Arbitrary metadata about the attack execution

`AttackResult` objects provide comprehensive reporting on attack campaigns, enabling analysis of red teaming effectiveness and vulnerability identification.
