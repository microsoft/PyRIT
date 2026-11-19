# Learning Resources

A short, opinionated reading and watching list for people getting into AI red teaming. It goes with [Learn AI Red Teaming](./0_red_teaming.md), which explains how we think about the work. If you only have time for a few things, take the first section in order.

This list was last reviewed in November 2026. The field moves quickly, so newer material exists for most topics. Spotted something that belongs here? [Open an issue or a pull request](../contributing/README.md). Inclusion is not an endorsement of any product.

## Start Here

1. **[Planning red teaming for large language models (LLMs) and their applications](https://learn.microsoft.com/en-us/azure/foundry/openai/concepts/red-teaming)** (Microsoft Learn).
   The questions to ask before you test anything: who tests, what to test, at which layer, and how to record results. Generic, short, and still current.
2. **[AI Red Teaming 101](https://learn.microsoft.com/en-us/security/ai-red-team/training)** (Microsoft AI Red Team, video series).
   Ten short episodes: what AI red teaming is, how generative models work, direct and indirect prompt injection, single-turn and multi-turn attacks, defenses, and three episodes on automating attacks with PyRIT. The episodes are modular, so you can skip around. Also available as a [YouTube playlist](https://www.youtube.com/playlist?list=PLlrxD0HtieHhXnVUQM42aKRPrirbUIDdh).
3. **[AI Red Teaming Playground Labs](https://github.com/microsoft/AI-Red-Teaming-Playground-Labs)** (Microsoft, hands-on).
   Twelve challenges from easy to hard that you run locally with Docker: credential exfiltration, system prompt extraction, multi-turn Crescendo attacks, and indirect prompt injection. They accompany the video series and include notebooks that solve some of the challenges with PyRIT. You need an OpenAI or Azure OpenAI key.
4. **[Lessons From Red Teaming 100 Generative AI Products](https://arxiv.org/abs/2501.07238)** [@bullwinkel2025airtlessons].
   Eight lessons and five case studies from the Microsoft AI Red Team. If you want the summary first, read [3 takeaways from red teaming 100 generative AI products](https://www.microsoft.com/en-us/security/blog/2025/01/13/3-takeaways-from-red-teaming-100-generative-ai-products/).

## Guides and Frameworks

Structured references for planning an engagement and for naming what you find.

- **[OWASP GenAI Red Teaming Guide](https://genai.owasp.org/resource/genai-red-teaming-guide/)** (2025) and **[GenAI Red Teaming Manual v1.0](https://genai.owasp.org/resource/genai-red-teaming-manual-version-1-0/)** (2026). The guide is an orientation. The manual is an eight-phase methodology from planning to remediation that covers models, applications, multimodal interfaces, and agents.
- **[OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/)** and **[OWASP Top 10 for Agentic Applications](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/)**. Common vocabulary for the most frequent risks. Useful as a checklist of things to consider, not as a test plan.
- **[MITRE ATLAS](https://atlas.mitre.org/)**. A knowledge base of adversary tactics and techniques against AI systems, modeled on MITRE ATT&CK, with real-world case studies.
- **[NIST AI 600-1: Generative AI Profile](https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf)** (2024). The generative AI companion to the NIST AI Risk Management Framework. Good for understanding how red teaming fits into risk management as a whole.
- **[Microsoft AI Red Team on Microsoft Learn](https://learn.microsoft.com/en-us/security/ai-red-team/)**. Hub page for threat modeling guidance, the failure mode taxonomy, and the AI bug bar.
- **[Taxonomy of Failure Mode in Agentic AI Systems](https://cdn-dynmedia-1.microsoft.com/is/content/microsoftcorp/microsoft/final/en-us/microsoft-brand/documents/Taxonomy-of-Failure-Mode-in-Agentic-AI-Systems-Whitepaper.pdf)** [@bryan2025agentictaxonomy]. What changes when the system can take actions.

## How Different Teams Do It

There is no single agreed way to red team AI systems. Reading how several teams describe their own practice shows where they agree and where they differ.

- **[Microsoft AI Red Team building future of safer AI](https://www.microsoft.com/en-us/security/blog/2023/08/07/microsoft-ai-red-team-building-future-of-safer-ai/)** (Microsoft, 2023). How AI red teaming differs from traditional red teaming.
- **[Challenges in red teaming AI systems](https://www.anthropic.com/news/challenges-in-red-teaming-ai-systems)** (Anthropic, 2024). A survey of the methods one lab uses, from expert and crowdsourced testing to automated red teaming, and why the lack of standard practices makes results hard to compare.
- **[OpenAI's Approach to External Red Teaming for AI Models and Systems](https://arxiv.org/abs/2503.16431)** [@ahmad2025externalredteaming]. Who to bring in, what access to give them, and what guidance they need.
- **[Google's AI Red Team: the ethical hackers making AI safer](https://blog.google/innovation-and-ai/technology/safety-security/googles-ai-red-team-the-ethical-hackers-making-ai-safer/)** (Google, 2023).

## Keeping Yourself Honest

Red teaming is easy to do badly and easy to oversell. These are worth reading before you report a number to anyone.

- **[Red-Teaming for Generative AI: Silver Bullet or Security Theater?](https://arxiv.org/abs/2401.15897)** [@feffer2024redteaming]. Surveys how much red teaming exercises differ in purpose, scope, and what they report, and argues that treating red teaming as a cure for every risk verges on security theater. Ends with a bank of questions to ask about any red teaming exercise.
- **[Red Teaming AI Red Teaming](https://arxiv.org/abs/2507.05538)** [@majumdar2025redteaming]. Argues that current practice focuses on flaws in individual models and overlooks the larger system of models, users, and environments.
- **[Comparison requires valid measurement: Rethinking attack success rate comparisons in AI red teaming](https://arxiv.org/abs/2601.18076)** [@chouldechova2026comparison]. Argues that many conclusions drawn from comparing attack success rates are apples-to-oranges comparisons, and sets out when such numbers can be compared at all.
- **[Effective Automation to Support the Human Infrastructure in AI Red Teaming](https://arxiv.org/abs/2503.22116)** [@zhang2025effectiveautomation]. Argues for combining human expertise with automated tools, and looks at what automation means for the people doing the work.
- **[Scoring Scorers](../blog/2026_04_14_scoring_scorers.md)** (PyRIT blog). How we measure the automated judges that PyRIT relies on, and why we had to.

## Practice

- **[AI Red Teaming Playground Labs](https://github.com/microsoft/AI-Red-Teaming-Playground-Labs)**. Described under Start Here above.
- **[Gandalf](https://gandalf.lakera.ai/)** (Lakera). Prompt injection challenges that run in the browser with no setup.
- **[HackAPrompt](https://www.hackaprompt.com/)**. A prompt injection competition. PyRIT has a [HackAPrompt target](../code/targets/hack_a_prompt_target.ipynb) if you want to attack the challenges with code.
- **[Web LLM attacks](https://portswigger.net/web-security/llm-attacks)** (PortSwigger Web Security Academy). Labs on attacking LLMs that are integrated into web applications, for people coming from web security.
- **[AI Village](https://aivillage.org/)**. The community behind the AI red teaming events at DEF CON.
- **Your own endpoint.** The exercise in [Learn by Doing](#learn-by-doing) needs nothing but a model you are allowed to test.

## Keeping Up

- **[Embrace The Red](https://embracethered.com/blog/)** (Johann Rehberger). Detailed write-ups of real prompt injection and data exfiltration findings in shipped products.
- **[Simon Willison on prompt injection](https://simonwillison.net/series/prompt-injection/)**. A long-running series that tracks the problem and the attempts to solve it.
- **[PyRIT blog](../blog/README.md)** and **[Discord](https://discord.gg/9fMpq3tc8u)**.
- **[Bibliography](../bibliography.md)**. The papers behind the attacks, converters, and datasets implemented in PyRIT. Picking one and reproducing it is good practice.

## About PyRIT

- **[PyRIT: A Framework for Security Risk Identification and Red Teaming in Generative AI Systems](https://arxiv.org/abs/2410.02828)** [@lopez2024pyrit]. The original paper.
- **[PyRIT: Democratizing AI Red Teaming Through Open-Source Tooling](https://commandline.microsoft.com/wp-content/uploads/2026/08/PyRIT_Whitepaper_2026.pdf)** [@lutz2026pyrit]. The 2026 whitepaper.
