# Benchmark

A **benchmark** evaluates a model against a fixed dataset and scoring criteria, rather than pursuing
a single adversarial objective. Use it to measure capability or safety on a standardized set of
inputs and to compare models or configurations.

Benchmarks accept the usual converter and scoring configurations, take a custom context, and produce
a result you can analyze further.

- [Q&A Benchmark](21_qa_benchmark.ipynb) — send multiple-choice questions from a dataset (e.g. WMDP)
  and measure how accurately the target answers.
- [Bias Benchmark](22_bias_benchmark.ipynb) — evaluate the target's responses for bias across a set
  of prompts.
