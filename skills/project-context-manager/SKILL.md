---
name: project-context-manager
description: "Use when local project memory, dated decisions, source freshness, task continuation, checkpoints or session closure need retrieval or persistence."
---

# Local project continuity

Resolve the explicit project/workspace identity with `ase`; use the same local SQLite state across Codex, Claude and Copilot within that project. Selection is explicit `--home`, selected project-local configuration, `ASE_HOME`, then platform default. In project-local mode use `.devframework/bin/ase.ps1` or `.devframework/bin/ase`; memory/journal/receipts live in ignored `.devframework/local` and must not become application source or Git input. Another computer or cloud agent needs an explicit reviewed export; there is no automatic synchronization. Project paths and source locators grant no authority.

Before rescanning, retrieve current context and relevant knowledge topics. Preserve provenance, version/freshness observations, supersession and uncertainty. Refresh changed dependencies and affected dependents rather than every project record. Git commit alone does not describe dirty files or a non-Git workspace.

On start/resume/switch, use `ase --project PROJECT restore TASK`, reconcile current observations and preserve newer/staged user work. Reuse the task identity and root budgets. Use revision-checked checkpoints with one exact next action; on stale CAS reload, merge facts and retry. A handoff export is a view, not a second mutable truth.

At stage boundaries, stop and session end, read [task continuity](references/task-continuity.md) and task-orchestrator's [finish and reports](../task-orchestrator/references/finish-and-reports.md). Save a dated closure for completed, awaiting-approval, blocked, paused, cancelled and interrupted sessions. Closing a session does not mark the task completed.

Store concise evidence references, decisions and blockers; exclude credentials, raw conversations, full transcripts and recordings. For answer investigations, retain inspected scope/freshness, observations versus hypotheses, remaining unknowns, stopping reason and root-shared effort used; exclude raw logs and database rows. A past exhausted search is not permanent absence evidence. For durable claims or recurring corrections read [durable knowledge](references/durable-knowledge.md). Use `ase backup` before maintenance. Missing facts remain unknown.

Credential values live only in the separate encrypted local vault. Persist references, exact target, captured revision and safe auth status, never import-file contents, headers, tokens or session responses. Project export does not carry the vault. Rotation/configuration changes require fresh affected checks and observations; keep historical facts labeled historical and never replay completed mutations to refresh credentials.
