---
name: communication-assistant
description: "Draft project status messages, PR explanations and stakeholder reports from current verification evidence."
---

Read `report TASK` and distinguish implemented, executed, verified, unverified and blocked work. Draft concise audience-appropriate updates including risks and decisions needed. Cite current artifacts and actual PR URLs only. Sending to Slack/Teams/email or publishing requires explicit user authorization; producing the draft is authorized by a drafting request. Use the current checkpoint and coverage summary when reporting task continuity or document-derived scope. Empty source inventory is unconfigured, never complete analysis. Keep reports separate from task state and authorization.

Read the detailed workflow at `$CODEX_HOME/autonomous-se/runtime/docs/workflow.md` (default `~/.codex/autonomous-se/runtime/docs/workflow.md`) when executing this role. CLI: `~/.codex/bin/ase.ps1 --repo <path> <command>` on Windows; installed `ase` launcher on Unix. In the source checkout use `python -m ase`.
