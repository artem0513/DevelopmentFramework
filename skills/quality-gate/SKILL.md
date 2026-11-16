---
name: quality-gate
description: "Use when current managed-task evidence must determine completion readiness or authorized publication eligibility."
---

# Evidence gate

Apply relevant [evidence decisions](../task-orchestrator/references/evidence-decisions.md) in independent judgments: required readiness, current artifact purpose/state/version, bounded claims and meaningful semantic checks. Preparing a script or observing an object is not evidence of execution or required consumer behavior. The runtime gate validates configured evidence; these semantic rules still need actual independent assessment.

Run `ase --project PROJECT gate TASK` and `report TASK`. Only current source/contract/execution-plan evidence can authorize COMPLETED; session closure and process exit zero do not imply approval. Respect the configured acceptance profile and parent coverage for mixed work.

Software retains successful mandatory commands, positive executed test counts, no skipped required tests, independent AC/code review and no P0/P1/P2 findings. P3 is advisory. Other profiles still require executable facts or explicitly specified confirmations and separate substantive judgments, rather than software-shaped placeholders. Reject stale, missing, expired, tampered or forged evidence.

Configured software also requires current independent [test adequacy](references/test-adequacy.md): original risk scenarios, meaningful actual assertions and required consumer boundaries. Legacy unconfigured contracts remain readable and must not be described as having passed adequacy. Before completion the original lead processes current result/coverage events through its [durable inbox](../task-orchestrator/references/coordinator-events.md); handling an event is not semantic approval.

Inspect blocked reasons separately from the overall verdict: any unresolved blocker stops mutation even if the overall verdict is REJECTED. In-scope repairs require fresh affected checks and independent receipts within persisted limits. Missing capabilities are not PASS and cannot be repaired by editing status fields.

Gate exit codes are 0 APPROVED, 2 REJECTED, 3 BLOCKED. Publication remains a dry run until explicitly authorized for the exact destination. Local receipts and hooks are a reliability layer; trusted CI and server-side rules are needed for enforcement against a hostile same-user process.
