# Autonomous SE workflow

Use `$CODEX_HOME/bin/ase.ps1` on Windows (default `~/.codex/bin/ase.ps1`), or the installed `ase` launcher on Unix. From this checkout use `python -m ase`. Always pass `--repo <canonical repository>` before the command. State is outside the repository and isolated by canonical path.

## Understand and plan

Inspect existing AGENTS, project docs, manifests, business logic and configured test commands. Retrieve `context` on demand. Record original request, functional/nonfunctional requirements, AC identifiers, assumptions with CONFIRMED/UNRESOLVED status, risks, boundaries, affected modules, dependencies and regression checks. Keep trivial tasks concise. Ask only about material unresolved business decisions. An unresolved mandatory criterion prevents approval.

Create a task JSON using templates/task.json. ACs must come from the user's requirement, not be reverse-engineered from the implementation. Assign command IDs that exercise each requirement and explicitly record manual checks. Inspect all configured argv before executing: repository scripts are untrusted code. Never auto-run privileged deployment/migration/destructive commands.

`init` discovers stack/test conventions; `start path/to/task.json` persists the contract and source baseline. Review the discovered plan rather than assuming every project uses standard npm scripts. Supply configured build/lint/static/security/integration/smoke commands appropriate to the actual project. Missing infrastructure stays BLOCKED; suite omission is not a successful test.

For framework work, consult docs/frameworks.md from the installed runtime. Use installed vendor skills matching the actual technology. Fetch a missing vendor skill using the official pinned source after inspecting its instructions. The matching SDK documentation remains the authority for APIs.

## Implement and delegate

For independent modules, delegate bounded implementation tasks with explicit file ownership, dependencies and integration order. Use separate worktrees for overlapping write-heavy work. Keep a single owner for shared files. Sequential execution is preferable when coordination would cost more than it saves.

Use Superpowers planning, debugging, TDD and review practices where relevant; this system adds evidence storage and gate semantics. Current user authorization takes precedence over any upstream workflow's optional approval rituals. Keep external sends, destructive actions and remote publishing under the user's explicit authorization.

`run TASK` launches the implementation then checks and independent verification with a bounded attempt count. `resume TASK` reopens persisted progress; inspect status after interruptions before retrying. A restarted task reruns affected checks. Do not automatically repeat deployments or other destructive side effects. When using native interactive subagents instead, they can inspect the code and generate tests, but the CLI's `verify` is still needed to create independent runner receipts.

## Verify and review

`check TASK` actually executes argv in the repository. It records timestamps, working directory, exit codes, log hashes, test counts and source fingerprints. `verify TASK` launches separate read-only Codex sessions for AC verification and review, not an implementation continuation. They receive original request, confirmed ACs, source baseline/diff, code and real execution artifacts. They must read actual code and evidence rather than implementation summaries. A separate model session is independent judgment, not an independent operating-system identity.

In a chat, prefer the configured `autonomous_se` MCP tools after executing `check` in the existing sandbox: call `verification_start` with the absolute repository and task_id, then poll `verification_status` until terminal. Inspect `result.report.quality_gate.status`; COMPLETED alone is not approval. This host coordinator launches separate read-only reviewers without starting a new harness inside the restricted shell. The direct nested CLI cannot write the real Codex home's required initialization state. If the MCP service is unavailable or refuses the repository scope, keep the task BLOCKED; preserve the sandbox and never manufacture runner receipts. See [ADR 002](ADR-002.md).

AC results: PASS/FAIL/UNVERIFIED/BLOCKED/NOT_APPLICABLE, each with expected behavior, implementation evidence, method, confidence limitations and missing coverage. Mandatory ACs require PASS and command-backed evidence. Unsupported assertions, skipped tests and missing infrastructure never count as PASS. Review findings require severity, file and line, explanation and reproduction when feasible. P0, P1 and P2 findings prevent approval; P3 is advisory. After an authorized in-scope fix, automatically rerun tests and launch fresh independent AC/review sessions before recomputing the gate. Fixes invalidate old evidence; neither old PASS results nor a severity downgrade can approve new source. Escalate after configured max_attempts; do not silently relax the contract.

`gate TASK` is deterministic and fail-closed. It validates actual runner receipts, artifacts and current source association. Editing a JSON status alone cannot create a missing command receipt or independent execution. A failed/blocked gate returns a nonzero exit. LOW risk may use fewer relevant commands, but independent acceptance and review remain mandatory. HIGH risk needs broader regression/security checks and human review of destructive/data/security decisions.

## Report and publish

`report TASK` produces a truthful report. Describe implemented, executed, independently verified, unverified and blocked behavior separately. Include original request, changed files, AC results, findings, build/tests/counts, risks, Git status, revision, gate and PR link only if one actually exists. Never describe an unavailable CI or a protocol stub as validated production enforcement.

Publishing is dry-run by default. `authorize --remote origin` records permission only after the human explicitly authorizes that repository and remote. `publish TASK --execute` requires approval, clean/intended changes and a feature branch. Never authorize a real repository as a convenience for a demonstration. Use a disposable local bare remote for demos. Never force push or merge automatically. A changed revision requires fresh evidence. PR creation is optional and should include the actual verification summary.

Git hooks are a local accident barrier. Configure server-side required checks and protect the trusted gate/policy using CODEOWNERS and environment permissions before calling publishing enforced. Local file hashes are not signatures and a same-user process can alter the runner/database; prompts cannot solve this security boundary.

## Memory and time

Use `context`, session summaries and decisions for concise retrieval; store unresolved questions. Store neither raw conversations nor credentials. `backup` uses SQLite's backup API. Each repository has a separate database; selecting another --repo must not retrieve another client's records. Review logs before sharing: generic redaction cannot guarantee removal of arbitrary business secrets.

Time entries classify agent runtime separately from human work. Human/billable durations need explicit confirmation; never derive them from model elapsed time. Daily/weekly summaries are drafts. Communication drafts stay local until the user explicitly authorizes sending to a named destination.

Use `weekly --date YYYY-MM-DD` for the seven stored start-dates ending at the selected date; timezone offsets are retained, not silently converted into local billing days. `correct-time ENTRY --reason ...` records before/after values atomically and resets confirmation unless explicitly confirmed again. `session-summary TASK --summary ... --decision ... --unresolved ...` persists concise context. Known verbose logs/handoffs expire after 30 days on the next project open (`ASE_LOG_RETENTION_DAYS=1..365`); active/stale locked tasks are preserved until recovery. Expired evidence requires new checks.

## Reusable project continuity

For new-project adoption, interrupted tasks or document-derived requirements, read [universal template](universal-template.md). Use one SQLite checkpoint with revision checks for the current handoff, restore actual Git observations before continuing, and use the optional source ledger only when relevant. Profile worksheets grant no permission and preselect no sources.

## Bounded correction loop

`run`/`resume` are trusted CLI-controller entrypoints. A rejected review (including P2) feeds the actual prior check, AC and review evidence into the next implementation attempt. Each completed cycle preserves its source-bound evidence under the task cycles archive before later verification replaces the current files. Verbose archive logs follow the existing retention policy; this is local application history, not hostile same-user immutability.

In an ordinary chat, use the same bounded loop with existing sandbox implementation/check tools and the verification-only MCP host: fix within the confirmed scope, check, start/poll fresh independent sessions, inspect gate; on P0/P1/P2 repeat without asking again for already-authorized work. Persist attempt/next-action context before interruptions. Do not start a nested CLI verifier or add host implementation/check/publish tools. Missing infrastructure or new material scope/authorization decisions stop the loop. An exhausted max_attempts remains unapproved and requires escalation; resume does not reset the counter. Interrupted final-attempt verification must finish against current source before deciding escalation.

Gate output preserves rejected_reasons and blocked_reasons separately. REJECTED remains the overall verdict when both occur, but the controller and chat agent must stop automatic mutation whenever blocked_reasons is nonempty. An explicit resume may perform one fresh check/independent-verification recovery probe without consuming or resetting implementation attempts; only complete actionable evidence permits another repair.
