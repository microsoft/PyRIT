# Workflow

A **workflow** orchestrates a multi-step operation that doesn't fit the single-objective attack,
benchmark, or prompt-generation moulds. It is the most generic executor category — reach for it when
you need to coordinate several systems around one goal.

The built-in examples are **cross-prompt injection attacks (XPIA)**, where the payload is hidden in
content a target system later processes (a webpage, a PDF, a résumé) rather than sent to the model
directly:

- [Website XPIA](18_xpia_website.ipynb) — hide a jailbreak in an uploaded webpage and let a
  summarization agent trigger it.
- [AI Recruiter XPIA](19_xpia_ai_recruiter.ipynb) — hide instructions in a résumé processed by an
  applicant-tracking agent.
