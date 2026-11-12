# Finish and reports

Use at completed stages, awaiting approval, blocked/paused/cancelled work, interruptions and requested stop.

Resolve the current task and context/checkpoint revisions. Prepare a concise session closure using the bundled session-finish template: outcome, actual checks, decisions, unresolved blockers, source/artifact references, reason and one precise next action. Run `ase --project PROJECT session finish TASK --session-id SESSION --closure-key KEY --expected-revision N --expected-context-revision M --file CLOSURE_JSON`. The same key/payload is idempotent; a changed payload conflicts. On stale revisions reload and reconcile before retrying. Closure is atomic; it does not imply task completion. Only satisfied current obligations plus APPROVED permit COMPLETED.

Store dated, concise local records rather than raw chats, full transcripts, credentials or binary recordings. Record occurred UTC time separately from recorded UTC time and retain corrections/history. On continuation restore existing task identity and reconcile current observations before mutation.

Use `ase --project PROJECT worklog --from YYYY-MM-DD --until YYYY-MM-DD --timezone Europe/Bucharest` or `meeting-report` for local calendar dates; `--until` is exclusive. Missing timezone rules block the local summary instead of silently using the host zone. Worklog reports events; meeting reports include results, decisions, blockers and next steps. `--as-of-end` reports historical state; current carried blockers must be labelled. These commands do not infer billable hours or change legacy daily/weekly time semantics.

Give a short final report separating implemented work, executed checks, independent judgments, unavailable checks and actual publication. Drafting reports does not authorize external sending.
