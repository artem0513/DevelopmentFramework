---
name: communication-assistant
description: "Use when a managed task needs a concise proposal, final status, meeting summary, PR explanation or stakeholder draft based on current evidence."
---

# Concise task communication

Before execution, prepare a roughly 5–8-line proposal: expected result; recommended business approach and reason; technical means and reuse; 2–4 main steps; verification; material assumptions/limits. Keep the detailed contract separately inspectable. Reuse existing approval for the same scope. Ask only a genuine material blocker after recommending an interpretation and explaining its reason and consequences; use task-orchestrator's [questions and approval](../task-orchestrator/references/questions-and-approval.md).

For status read `ase --project PROJECT report TASK`, the current checkpoint and source coverage. Distinguish implemented, executed, independently verified, unavailable, blocked and actually published results. Cite existing artifacts and actual URLs. Empty source inventory is unconfigured, not completed analysis.

For dated activity use `ase worklog` and `ase meeting-report` with explicit date bounds and timezone. Draft results, decisions, blockers and next actions; label current versus historical/as-of blockers. Read [finish and reports](../task-orchestrator/references/finish-and-reports.md) before session closure or requested stop. Summaries do not alter task approval or evidence.

Drafting is authorized by a drafting request. Sending to email/chat or publishing needs explicit authorization for the destination. Never infer billable hours from agent activity.
