# Scoring

Scoring is a main component of the PyRIT architecture. It is primarily used to evaluate what happens to a prompt. It can be used to help answer questions like:

- Was prompt injection detected?
- Was the prompt blocked? Why?
- Was there any harmful content in the response? What was it? How bad was it?

This collection of notebooks shows how to use scorers directly. To see how to use these based on previous requests, see [the batch scorer](../scoring/7_batch_scorer.ipynb). Scorers can also often be [used automatically](../executor/attack/1_prompt_sending_attack.ipynb) as you send prompts.

There are two general types of scorers. `true_false` and `float_scale` (these can often be converted to one type or another). A `true_false` scorer scores something as true or false, and can be used in attacks for things like success criteria. `float_scale` scorers normalize a score between 0 and 1 to try and quantify a level of something (e.g. harmful content).

[Scores](../../../pyrit/models/score.py) are stored in memory as score objects.

## Setup
