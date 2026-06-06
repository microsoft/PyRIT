# Prompt Generator

A **prompt generator** produces adversarial prompts rather than running them against a target. Use it
to grow your datasets — seed it with examples or templates and let it generate variations you can
later feed to attacks or benchmarks.

Generators accept converter configurations and a custom context, and produce a result (some add
custom printers for readable output).

- [Anecdoctor](24_anecdoctor_generator.ipynb) — builds misinformation prompts from in-the-wild
  examples, either few-shot or via an extracted knowledge graph.
- [GPTFuzzer](25_fuzzer_generator.ipynb) — generates new jailbreak templates from existing ones using
  Monte Carlo Tree Search to balance exploring new templates against exploiting good ones.
