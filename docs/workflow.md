# Managed task workflow

Explicit adoption of a selected project/workspace activates this workflow. It supports software, infrastructure, documents, research and mixed work. A template, shared runtime or optional integration does not activate other projects. Ordinary questions use their domain workflow without a managed contract. Choose profiles by actual action: financial report warnings do not turn analysis into software debugging.

Use the installed `ase` / `ase.ps1` launcher, or `python -m ase` in the source checkout. Global `--project PROJECT_ID` (or compatibility `--repo ROOT`) and `--home` precede the subcommand. State selection is `--home`, `ASE_HOME`, then the platform AutonomousSE directory. Local project UUIDs and explicit workspace aliases isolate the memory; Git is not required.

## Intake and discovery

Retrieve existing task identity, `context`, relevant knowledge and `restore TASK` before replacing a task or rescanning known facts. Reconcile current observations with saved state; preserve newer user work. For assignments/meetings use task-intake, separating required outcomes, constraints, decisions, suggestions, contradictions and unknowns with document/user-message/timestamp references. A participant's suggestion is not an approval or external-action grant. Raw recordings and full transcripts stay outside SQLite.

Inspect named resources first. Expand only to material dependencies and record why. Use task-discovery to inspect applicable instructions, source versions/freshness and actual capabilities. A commit does not cover dirty/untracked content or external settings. Refresh affected knowledge and dependent records while retaining provenance/history. Read skill metadata first and load bodies/references only for the active step; optional methods do not start a second orchestrator.

Capability evidence separates presence/version, permission, structured output, resource access and actual independent sessions. Unknown is unavailable until probed. Local CLI adapters are sufficient; no framework MCP service is required. A protocol fixture or installed native role does not prove a subscribed backend or live independent session. Report unavailable infrastructure without copying credentials, relaxing permissions or installing unrequested products.

## Proposal and authorization

Give a roughly 5–8-line proposal: result; recommended business approach and reason; technical means/reuse; 2–4 steps; verification; material limits/assumptions. The detailed contract remains separately inspectable. Do not hide a material condition to meet the length target.

Ask only when the answer cannot be retrieved reliably, materially changes the outcome/compatibility/cost/risk/authority, and has no safe choice within approved scope. State a recommended interpretation, reason and consequences before the question. Choose routine internal details yourself. Continue independent permitted discovery while awaiting a blocking answer.

Persist the exact proposal hash, contract revision, approved stages, observed user-message reference, timestamp and external-action bounds via task propose/approve. A boolean or invented message reference is not approval. Reuse existing session authorization for the same outcome/scope. New business meaning, material risk/cost, resources or external authority require a revised proposal/approval. Internal decomposition may create subset stages without removing parent obligations. Execution-plan changes require fresh affected evidence without automatically revoking business approval.

## Execution and evidence

Use the configured local backend adapter and relevant domain skills. Preserve platform permissions. Perform real checks appropriate to the acceptance profile and keep executable facts separate from model judgments:

| Profile | Required evidence |
| --- | --- |
| software | Mandatory successful commands, positive executed tests, no skipped required tests, independent AC/code review, current source/contract/plan fingerprints |
| infrastructure | Actual resource IDs, before/after observations, recorded intents, reconciliation and independent postconditions |
| document | Required artifact/content, readable rendering when relevant, traceable sources and independent acceptance/substantive review |
| research | Dated sources, claim coverage, explicit inference/uncertainty and independent acceptance/substantive review |
| mixed | Mapping and complete coverage of all mandatory parent and child obligations |

`check TASK` executes configured checks; review argv before running project scripts. `verify TASK` captures separate independent acceptance and substantive review sessions through the authorized adapter. A model reply alone is not a runner receipt. Verification uses original requirements, actual current artifacts and execution evidence, rather than trusting an implementation summary. Missing independent execution remains BLOCKED.

`gate TASK` validates current evidence and profile coverage; exit codes are 0 APPROVED, 2 REJECTED, 3 BLOCKED. P0/P1/P2 block approval; P3 is advisory. Missing, stale, tampered, expired or forged evidence cannot approve. Session closure and process exit zero do not establish completion. Only satisfied current obligations plus APPROVED permit COMPLETED.

## Optional parallel work

Parallel work is optional when at least two ready parts are independent and native host/provider subagents are available and permitted. One lead retains the approved goal, scope and budget, assigns each bounded goal/read-write ownership/inputs/output/acceptance/dependencies, gathers results and integrates before final affected checks and independent review. Use separate authorized workspaces for disjoint mutations or return proposed edits to the lead; preserve shared-root execution leases, CAS and actual receipt capture.

Recompute the dependency graph's ready set after every result, failure, new dependency or capacity change. A sequence of ready waves may be 1 → 4 → 1 → 5; concurrency is bounded by actual provider slots, resources and remaining budget, and excess work waits in a queue. Dependencies and integration barriers can reduce concurrency, then release more work. This internal scheduling needs no repeated interview within approved scope. Trivial work or unavailable optional native tools uses the serial path; no second coordinator, parallel SDK or MCP is mandatory. Advisory native answers are not formal AC/review receipts. Read the conditional parallel-work reference in the canonical orchestrator package when dispatching.

This policy combines dynamic worker decomposition and synthesis ([Anthropic](https://www.anthropic.com/research/building-effective-agents)), a lead retaining the answer while using bounded specialists ([OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration)), runtime-sized fan-out and deliberate state merging ([LangGraph](https://docs.langchain.com/oss/python/langgraph/graph-api#send)), and dependency/context/integration planning with coordination costs ([GitHub](https://docs.github.com/en/copilot/concepts/agent-capabilities/choosing-and-delegating-agent-work/delegating-and-parallelizing-work-with-agents)). The lightweight ready queue and optional native delegation are this framework's design inference; no new SDK, LangGraph or MCP dependency follows from those sources. Worker results carry status, evidence, proposed/actual changed paths and blockers so the lead can validate and combine them.

## Bounded repair and stopping

Automatically repair actionable in-scope findings, then rerun affected checks and fresh independent AC/review. Existing approval covers those technical corrections. Persist at most three candidate cycles per stage, twelve per root task, one diagnostic pass per stage with a 1200-second timeout, and any stricter legacy limit. Resume, children and revised contracts do not reset root spending. A second same-cause failure invokes the one recorded diagnostic pass; a crash does not create a fresh allowance.

Inspect blocked reasons separately: any unresolved blocker stops mutation even if the overall gate is REJECTED. Explicit resume can probe restored checks/verification before another repair. Exhaustion, unavailable mandatory capability and new material conditions stay explicit; do not weaken tests or downgrade findings to obtain approval.

Persist stable operation IDs and intents before effects. Lost acknowledgement is UNKNOWN: reconcile the actual resource before retrying. Do not recreate/deploy/roll back blindly. On stop, stop scheduling mutations, account for active effects and save a checkpoint/closure. A hard kill preserves only the last completed transaction.

## Closure, memory and reports

Use revision-checked `checkpoint` and `session finish` records with concise results, actual checks, decisions, blockers, evidence references and one next action. The closure key/payload is idempotent; conflicting content or stale CAS requires reconciliation. Reasons include completed, awaiting approval, blocked, paused, cancelled and interrupted. Closing the session does not silently complete or cancel the task.

Use task-orchestrator's conditional finish-and-reports reference for the current CLI payload. Store occurred UTC separately from recorded UTC and retain corrections. `worklog --from DATE --until DATE --timezone Europe/Bucharest` reports dated events; `meeting-report` adds results, decisions and carried blockers, with historical `--as-of-end` separate from current status. The upper date is exclusive. Missing timezone rules block a local report rather than use the host timezone silently.

Local memory holds concise source-backed facts, versions and history, not raw chats, full transcripts, recordings or credentials. There is no automatic multi-computer/cloud synchronization. Export/import is an explicit reviewed transfer. Reports are drafts unless the user authorized a destination; measured agent runtime never becomes inferred billable human time. Legacy daily/weekly time-entry semantics remain unchanged.

`report TASK` distinguishes implemented work, executed checks, independent judgments, unavailable checks and actual publication. Publishing is a dry run unless explicitly authorized for the exact remote; protected-path/high-risk controls and Git hooks remain in force. Never force-push or auto-merge. Local receipts are not a security identity: enforce publishing using trusted CI and server policy. [ADR 004](ADR-004.md) explains the portable CLI boundary and the historical MCP transition.
