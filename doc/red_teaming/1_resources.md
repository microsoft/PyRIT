# Learning Resources

A short list for people getting into AI red teaming. It goes with [Learn AI Red Teaming](./0_red_teaming.md), which explains how we think about the work.

This list was last reviewed in November 2026. Spotted something that belongs here? [Open an issue or a pull request](../contributing/README.md).

## Start Here

- **[Planning red teaming for large language models (LLMs) and their applications](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/red-teaming)** (Microsoft Learn). The questions to ask before you test anything: who tests, what to test, at which layer, and how to record results.
- **[Lessons From Red Teaming 100 Generative AI Products](https://arxiv.org/abs/2501.07238)** [@bullwinkel2025airtlessons]. Eight lessons and five case studies from the Microsoft AI Red Team. For the short version, read [3 takeaways from red teaming 100 generative AI products](https://www.microsoft.com/en-us/security/blog/2025/01/13/3-takeaways-from-red-teaming-100-generative-ai-products/).

## Guides and Frameworks

- **[OWASP GenAI Red Teaming Guide](https://genai.owasp.org/resource/genai-red-teaming-guide/)** (2025) and **[GenAI Red Teaming Manual v1.0](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/)** (2026). The guide is an orientation. The manual is an eight-phase methodology from planning to remediation that covers models, applications, multimodal interfaces, and agents.
- **[OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)** and **[OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)**. Common vocabulary for the most frequent risks. Useful as a checklist of things to consider, not as a test plan.
- **[MITRE ATLAS](https://atlas.mitre.org/)**. A knowledge base of adversary tactics and techniques against AI systems, modeled on MITRE ATT&CK, with real-world case studies.
- **[NIST AI 600-1: Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)** (2024). The generative AI companion to the NIST AI Risk Management Framework. Good for understanding how red teaming fits into risk management as a whole.
- **[Microsoft AI Red Team on Microsoft Learn](https://learn.microsoft.com/en-us/security/ai-red-team/)**. Hub page for threat modeling guidance, the failure mode taxonomy, and the AI bug bar.
- **[Taxonomy of Failure Mode in Agentic AI Systems](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/final/en-us/microsoft-brand/documents/Taxonomy-of-Failure-Mode-in-Agentic-AI-Systems-Whitepaper.pdf)** [@bryan2025agentictaxonomy] and its **[v2.0 update](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/bade/documents/products-and-services/en-us/security/Taxonomy-of-Failure-Modes-in-Agentic-AI-Systems-v2-0.pdf)** (2026). What changes when the system can take actions.

## Blog Posts and Talks from the Microsoft AI Red Team

- **[Microsoft AI Red Team posts on the Microsoft Security Blog](https://www.microsoft.com/en-us/security/blog/author/microsoft-ai-red-team/)**. The team's author page, with the most recent posts first.
- **[Updating the taxonomy of failure modes in agentic AI systems: What a year of red teaming taught us](https://www.microsoft.com/en-us/security/blog/2026/06/04/updating-taxonomy-failure-modes-agentic-ai-systems-year-red-teaming-taught-us/)** (blog, 2026).
- **[Mitigating Skeleton Key, a new type of generative AI jailbreak technique](https://www.microsoft.com/en-us/security/blog/2024/06/26/mitigating-skeleton-key-a-new-type-of-generative-ai-jailbreak-technique/)** (blog, 2024) [@microsoft2024skeletonkey].
- **[How Microsoft discovers and mitigates evolving attacks against AI guardrails](https://www.microsoft.com/en-us/security/blog/2024/04/11/how-microsoft-discovers-and-mitigates-evolving-attacks-against-ai-guardrails/)** (blog, 2024). Introduces Crescendo and Spotlighting.
- **[Announcing Microsoft's open automation framework to red team generative AI systems](https://www.microsoft.com/en-us/security/blog/2024/02/22/announcing-microsofts-open-automation-framework-to-red-team-generative-ai-systems/)** (blog, 2024). The post that announced PyRIT.
- **[Microsoft AI Red Team building future of safer AI](https://www.microsoft.com/en-us/security/blog/2023/08/07/microsoft-ai-red-team-building-future-of-safer-ai/)** (blog, 2023). How AI red teaming differs from traditional red teaming.
- **[Lessons From Red Teaming 100 Generative AI Products](https://www.youtube.com/watch?v=qi2DneFkRf4)** (talk, BlueHat 2024).
- **[A Few Useful Lessons About AI Red Teaming](https://www.youtube.com/watch?v=WLBuv3JnVnk)** (talk, BlueHat 2023).
- **[Red Teaming LLMs and GenAI with PyRIT](https://talkpython.fm/episodes/show/521/red-teaming-llms-and-genai-with-pyrit)** (podcast, Talk Python To Me, episode 521).

## Practice

Places to try attacks against systems that were built to be attacked.

:::{important}
We do not endorse or recommend these sites, their operators, or any product or service they offer. They are listed as examples only. Any interaction is between you and the operator: read their terms, and decide for yourself what you share with them.
:::

- **[HackAPrompt](https://www.hackaprompt.com/)**. A prompt injection competition. PyRIT has a [HackAPrompt target](../code/targets/hack_a_prompt_target.ipynb) if you want to attack the challenges with code.
- **[Agent Breaker](https://play.lakera.ai/agent-breaker)** (Lakera). Browser-based challenges against mock AI applications.
- **[Web LLM attacks](https://portswigger.net/web-security/llm-attacks)** (PortSwigger Web Security Academy). Labs on attacking LLMs that are integrated into web applications, for people coming from web security.
- **[AI Village](https://aivillage.org/)**. A community for hands-on AI security education and red team practice, known for its events at DEF CON.
- **Your own endpoint.** The exercise in [Learn by Doing](#learn-by-doing) needs nothing but a model you are allowed to test.
