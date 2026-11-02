This repository implements Autonomous SE. Read docs/ADR-001.md for the trust boundary and docs/research.md for selected upstream components.

Use Python 3.11+ standard library for the runtime. Run `python -m unittest discover -s tests -v` after substantive runtime changes. Keep independent AC and review sessions separate from implementation; a deterministic adapter is only a protocol test, never independent semantic validation.

Treat this tool as a local reliability layer. Only a trusted CI runner plus server-side branch protection can enforce publishing against a hostile same-user agent. Reports must distinguish executed tests, independent judgments and unavailable enforcement.
