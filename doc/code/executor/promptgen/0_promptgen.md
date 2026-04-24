# Prompt Generator

The Prompt Generator is used to create adversarial prompts using various strategies. They are a useful component to use to augment your prompt dataset(s) for evaluating the safety and security of generative AI systems.

Some strategies include:

- The [Anecdoctor](./1_anecdoctor_generator.ipynb) prompt generator develops adversarial prompts using in-the-wild examples through a few-shot prompting or knowledge graph-augmented approach.
- The [GPTFuzzer](./fuzzer_generator.ipynb) prompt generator uses a strategy based on GPTFuzzer by Yu et al. to generate new jailbreak templates from existing templates by applying various fuzzer converter techniques.

All generators have configurable converter configurations and custom contexts, and produce a result. Some have custom result printers to add better formatting.
