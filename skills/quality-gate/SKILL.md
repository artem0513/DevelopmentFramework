---
name: quality-gate
description: "Evaluate recorded build, test, independent AC and review evidence before completion or Git publishing."
---

Execute `gate TASK`; rely on its current-source receipts and exit status, not written PASS claims. Require mandatory AC evidence, positive test counts, successful required commands and no P0/P1/P2 findings. P3 remains advisory. Reject stale/tampered artifacts and protected modifications. Generate `report TASK`. Only APPROVED can proceed to configured publication. Hooks are bypassable locally; use protected trusted CI for enforcement.

Read the detailed workflow at `$CODEX_HOME/autonomous-se/runtime/docs/workflow.md` (default `~/.codex/autonomous-se/runtime/docs/workflow.md`) when executing this role. CLI: `~/.codex/bin/ase.ps1 --repo <path> <command>` on Windows; installed `ase` launcher on Unix. In the source checkout use `python -m ase`.
