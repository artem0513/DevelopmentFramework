---
name: task-orchestrator
description: "Use when a managed software, infrastructure, document, research or mixed task is requested in an explicitly adopted project or workspace."
---

# Managed task workflow

Use only in a project or workspace that explicitly adopted DevelopmentFramework. Adoption covers managed software, infrastructure, documents, research and mixed work; select the profile from the actual action. A financial report with warnings is a document or research task. A requested fix to its generator is software. Ordinary questions use their domain workflow without creating a managed task.

Read task-intake for new assignments and task-discovery before proposing implementation. Its scoped search for existing project examples is the default; an explicit user opt-out skips that search and example reuse, including cached/delegated examples. Preserve that choice before retrieving pattern records; necessary target/dependency inspection remains. Restore an existing task with `ase --project PROJECT restore TASK` before replacing it. Local SQLite records, sources and checkpoints are shared across backends on this computer and isolated by project identity.

Before execution give a concise proposal: outcome, recommended business approach and reason, technical means, 2–4 steps, verification and material assumptions. Record approval of the exact proposal hash and approved stages once. Existing user authorization persists; technical repairs and internal decomposition within it do not require another interview. Read [questions and approval](references/questions-and-approval.md) only when a decision or authorization boundary is unresolved.

Execute through the local `ase` CLI using the configured backend adapter and appropriate domain skills. When `.devframework/config.toml` selects project-local mode, use that project's `.devframework/bin/ase.ps1` or `.devframework/bin/ase`; its three bots share private state inside that project. Do not replace it with another project's/global home on an unknown UUID. The CLI is sufficient; a missing optional integration is not a workflow blocker. Discover actual independent-session capability and permission before promising verification. An installed skill or a protocol fixture does not establish live backend availability. Do not copy credentials or change provider permissions to make a missing capability appear available.

When an approved external service needs authentication, read [local credentials](references/local-credentials.md). Use shared metadata and credential references; import only a user-designated local file, let the local executor handle values, and reuse sessions/approval. Configure a supported CLI recipe within the approved bounds before asking the user to solve a technical setup detail. Credentials do not expand task authority.

When two or more ready parts are independent and useful to run together, use available permitted native subagents under [parallel work](references/parallel-work.md). Recompute ready work after results and dependencies; queue excess capacity and integrate through one lead. Reuse approval and budgets, preserve shared-root serialization, and fall back to serial work when optional delegation is unavailable.

Run meaningful checks and separate independent acceptance and substantive review against the original request. For software preserve positive executed test counts, successful mandatory checks and independent AC/code review. P0/P1/P2 block approval; P3 is advisory. Use `ase --project PROJECT gate TASK` and `report TASK`; a process finishing or a written PASS is not approval. Read [profile evidence](references/profile-evidence.md) when selecting checks.

Repair actionable in-scope findings automatically with fresh source-bound checks and fresh independent judgments. Respect persisted limits: at most three candidate cycles per stage, twelve per root task, one 1200-second diagnostic pass per stage, and any stricter legacy limit. Any unresolved blocked reason stops mutation even if the verdict is REJECTED. Resume preserves counters and reconciles unknown external effects before retrying. Read [stopping and recovery](references/stopping-and-recovery.md) on repeated failures or interruptions.

At completion, awaiting approval, blocker, pause, cancellation or requested stop, save a dated session closure and one exact next action. Read [finish and reports](references/finish-and-reports.md). Close a session without claiming the task completed. Publish or send only under the existing explicit destination authorization; default publication remains a dry run.
