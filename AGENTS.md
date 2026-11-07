This repository implements Autonomous SE. Read docs/ADR-001.md for the trust boundary and docs/research.md for selected upstream components.

Use Python 3.11+ standard library for the runtime. Run `python -m unittest discover -s tests -v` after substantive runtime changes. Keep independent AC and review sessions separate from implementation; a deterministic adapter is only a protocol test, never independent semantic validation.

Treat this tool as a local reliability layer. Only a trusted CI runner plus server-side branch protection can enforce publishing against a hostile same-user agent. Reports must distinguish executed tests, independent judgments and unavailable enforcement.

<!-- autonomous-se managed begin -->
This project explicitly adopts DevelopmentFramework for code or configuration implementation. For those tasks use .agents/skills/task-orchestrator/SKILL.md; delegate independent AC verification and code review and use the evidence gate before claiming verified completion or publishing. Routine domain analysis, financial reports, documents and questions use their relevant domain workflow; they do not activate this engineering process. Preserve project rules and user authorization.
<!-- autonomous-se managed end -->
