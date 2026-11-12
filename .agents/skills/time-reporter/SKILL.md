---
name: time-reporter
description: "Use when measured activity or explicitly supplied human durations need local time entries, timesheet drafts, corrections or dated activity summaries."
---

# Time and dated activity

Use isolated local `ase` time entries. Separate agent execution from human work and billable time. Use measured intervals or explicit user-provided durations; retain confirmation status and never infer billable hours from model elapsed time. Corrections preserve prior attribution and require a reason.

Legacy `daily` and `weekly` summarize stored time-entry start dates with their existing semantics; timezone offsets are not silently rewritten into billing days. `correct-time` retains atomic before/after values and resets confirmation unless explicitly confirmed again.

Use `ase --project PROJECT worklog --from DATE --until DATE --timezone Europe/Bucharest` for dated task events and `meeting-report` for results, decisions and blockers. The upper date is exclusive. Those views use occurred timestamps, retained corrections and explicit current/as-of blockers; they do not create human time entries. Missing timezone rules block a local summary instead of silently substituting host time.

Save session closures with project-context-manager when stopping or finishing. Draft timesheets stay local; sending or writing to an external timesheet needs explicit destination authorization.
