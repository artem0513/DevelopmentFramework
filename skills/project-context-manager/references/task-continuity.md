# Task continuity

Use on start, resume, task switching, significant changes, test results, decisions, blocker changes and before a final handoff. Resolve the canonical repository and existing task ID with `context`; source identity (for example a full issue URL) is a locator, never authorization. Reuse the same task across chats. Do not create chats automatically.

Run `restore TASK` before continuing. It returns the contract, checkpoint, source coverage, current Git observations and drift. Reconcile branch, full SHA, dirty/untracked work and errors against saved observations; preserve newer or staged work. Git errors are unknown, not a clean checkout. Reading state does not fetch, checkout, reset, publish or run project scripts.

Use `continuity-snapshot TASK` to obtain the expected checkpoint revision. Prepare a concise JSON payload using templates/checkpoint.json, then `checkpoint TASK --expected-revision N --file PATH`. Record stage, summary, one precise next_action, blockers and source_refs. The runtime captures Git observations. A stale revision fails; reload, merge facts and retry. Never bypass rejection with an old summary. Historical checkpoints remain in SQLite. Markdown exports are disposable views. Decisions and session summaries remain available for concise retrieval; they cannot overwrite the canonical checkpoint.

Store evidence references, not raw chats, secrets or full transcripts. Missing requirements, approvals or evidence remain unknown. A checkpoint is active-agent persistence, not guaranteed background autosave or a shutdown hook. An interruption preserves only the last completed transaction. Resume never means re-execute external side effects.
