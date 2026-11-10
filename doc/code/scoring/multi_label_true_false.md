# Multiple labeled true/false verdicts

Some classifiers return several independent answers from one inference. For example,
a response may refuse a harmful request without itself being harmful. Combining those
answers with OR would lose that distinction.

`MessageMultiLabelTrueFalseScorer` preserves one `Score` for each declared label in
`score_category`. Each supported message piece produces a complete set of labeled
verdicts. The message pipeline aggregates pieces within each label, using
`TrueFalseScoreAggregator.OR` by default. Existing `MessageTrueFalseScorer` subclasses
continue to return one aggregate verdict.

## Implementing a classifier

Declare nonempty, case-insensitively unique labels and implement
`_score_piece_with_expectation_async`. Return a separate true/false `Score` with
`score_category=[label]` for every declared label. Return `[]` when the classifier
does not apply to the evidence. Missing, duplicate, or unknown labels are rejected
before any scores are persisted.

The following synthetic example reads classifier output from JSON. With PyRIT
initialized, it can demonstrate the contract without calling a model:

```python
import json

from pyrit.models import ContentScorable, MessagePiece, Score, ScoringExpectation
from pyrit.score import (
    MessageMultiLabelTrueFalseScorer,
    ScorerPromptValidator,
    TrueFalseScoreSelector,
)


class ExampleClassifier(MessageMultiLabelTrueFalseScorer):
    def __init__(self) -> None:
        super().__init__(
            labels=["harmful_request", "response_refusal", "harmful_response"],
            validator=ScorerPromptValidator(supported_data_types=["text"]),
        )
        self.calls = 0

    async def _score_piece_with_expectation_async(
        self, message_piece: MessagePiece, *, expectation: ScoringExpectation | None
    ) -> list[Score]:
        self.calls += 1
        verdicts = json.loads(message_piece.converted_value)
        return [
            Score(
                score_value=str(value).lower(),
                score_type="true_false",
                score_category=[label],
                scorer_class_identifier=self.get_identifier(),
                message_piece_id=message_piece.id,
                scored_expectation=expectation,
            )
            for label, value in verdicts.items()
        ]


classifier = ExampleClassifier()
evidence = ContentScorable(
    value=json.dumps(
        {
            "harmful_request": True,
            "response_refusal": True,
            "harmful_response": False,
        }
    )
)
objective_scorer = TrueFalseScoreSelector(scorer=classifier, label="harmful_response")
scores = await objective_scorer.score_async(scorable=evidence)
print(classifier.calls, scores[0].get_value())
# 1 False
```

A concrete target-backed scorer owns its `TargetJudge`, renders its own prompt,
captures evidence with `_capture_judgment_evidence`, and passes the full expectation
in `JudgmentRequest`. Its identifier must include the target and any additional
behavioral configuration. The family base does not accept a `chat_target` parameter.
`MultiLabelTrueFalseScorer` provides the same result contract for other evidence types.

## Selecting, composing, and evaluating a verdict

Use `TrueFalseScoreSelector` for an attack's objective/refusal scorer, boolean
composition, or objective evaluation. The selector chooses by label, not output order,
and its identity includes both the label and the source scorer. It preserves the
source's target discovery and blocked-judge policy. Float thresholding is for float
scores, not multi-label boolean results.

One selector call returns one public projection and persists all labeled child
verdicts as intermediate scores. The projection has its own score ID and selector
identity. Default queries return public results. Query retained labels with
`include_intermediate=True`:

```python
from pyrit.memory import CentralMemory

memory = CentralMemory.get_memory_instance()
request_scores = await memory.get_scores_async(
    score_category="harmful_request",
    include_intermediate=True,
)
```

Calling the multi-label scorer directly returns and persists every label as a public
result. Reading saved scores does not perform another inference. Separate scoring or
selector calls can perform separate inferences; the selector does not cache them.

Unsupported roles or modalities return `[]`. Unreadable or fully blocked evidence
produces an undetermined score for each label. When `raise_if_scorer_blocks=False`,
a blocked judge response does the same, preserving any retained error observation.
The existing three-valued AND/OR aggregation applies independently to each label.
