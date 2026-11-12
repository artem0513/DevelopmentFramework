# Stopping and recovery

Use on a requested stop, repeated failure, unknown external effect, exhausted budget or interruption.

Persisted budgets belong to the root task. Resume, child creation and contract revisions do not reset them. Respect three candidate cycles per stage, twelve per task and any stricter legacy maximum. The second same-cause failure uses at most one diagnostic pass for the stage, recorded before launch, with a 1200-second timeout. Do not create another diagnostic after a crash or exhausted budget.

An unresolved blocked reason stops automatic mutation even when the gate also reports REJECTED. An explicit resume can probe restored checks/independent verification before repair, without resetting implementation attempts. New material conditions require a revised proposal and authorization; in-scope technical fixes reuse existing authorization.

Before an external effect, persist a stable action ID and intent. Lost acknowledgement means UNKNOWN: observe/reconcile the actual resource before retrying. Do not blindly recreate, redeploy or roll back. Keep unresolved operations explicit across task/session boundaries.

On stop, stop scheduling changes, settle or record running operations, and use project-context-manager plus finish-and-reports to persist a checkpoint, dated closure, blockers and one next action. A hard process kill preserves only the last completed transaction. Never describe an interrupted session as completed work.
