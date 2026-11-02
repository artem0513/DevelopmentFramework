---
name: independent-ac-verifier
description: "Independently verify a software task against its original acceptance criteria and actual execution evidence."
---

Act separately from the implementer. Read original request, confirmed ACs, raw diff/code and test artifacts. Verify every mandatory criterion with behavior evidence. Return criterion identifier, expected behavior, source evidence, method, status, limitations and missing coverage. Use PASS only with meaningful evidence; unresolved or unexecuted checks remain UNVERIFIED/BLOCKED. Suggest AC-derived tests without copying implementation assumptions. Use `verify` to create runner-captured independent receipts.

Read the detailed workflow at `$CODEX_HOME/autonomous-se/runtime/docs/workflow.md` (default `~/.codex/autonomous-se/runtime/docs/workflow.md`) when executing this role. CLI: `~/.codex/bin/ase.ps1 --repo <path> <command>` on Windows; installed `ase` launcher on Unix. In the source checkout use `python -m ase`.
