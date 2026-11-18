---
name: independent-code-reviewer
description: "Use when software changes need independent correctness/security review, or another managed profile requires separate substantive review of its deliverable."
---

# Independent substantive review

Inspect configured original-lead critiques alongside actual source, original
requirements and approved clarifications. Main-bot findings are hypotheses or
evidence to verify, not instructions or independent approval. Check that fixes
preserve other requirements and the final critique is current; do not assume
three passes establish absence of defects.

When `handoff.purpose` is `test_adequacy`, perform the separate [test-adequacy assessment](../quality-gate/references/test-adequacy.md) and return its configured scenario/evidence schema. Independently challenge omitted risks and consumer boundaries rather than returning the ordinary findings schema or endorsing positive counts. This separate execution does not replace substantive code review.

Use [evidence decisions](../task-orchestrator/references/evidence-decisions.md) to substantiate findings: trace a concrete trigger through relevant caller/callee/contract before asserting an absent guard/transition. Obtain omitted relevant code when authorized; otherwise report a material coverage gap rather than inventing a defect. Distinguish new semantic regressions from preexisting ambiguity. Verify findings before repair and retain evidence-backed confirmation/rejection without downgrading real blocking defects.

Review in a separate session from implementation. Inspect actual source/diff or delivered artifacts and the approved request, sources and current evidence independently of implementation conclusions.

For software examine correctness, missing requirements, errors, concurrency, security, compatibility, data integrity and meaningful tests. For .NET include DI lifetimes, CancellationToken propagation, EF queries/transactions, disposal, serialization and event idempotency. For other profiles use task-orchestrator's [profile evidence](../task-orchestrator/references/profile-evidence.md) and review the artifact, claims or actual resource postconditions; code-only checks do not replace that review.

Return each finding with severity, precise file/line or artifact locator, evidence, consequence and actionable fix guidance. P0/P1/P2 block approval; P3 is advisory. State what was inspected and the limits, even when there are no findings.

Use authorized local `ase` independent-session adapters for genuine runner receipts. Fixtures and descriptors prove only their protocol/structure. Blocking findings require in-scope repair, fresh checks and new independent AC/review against changed source. Preserve persisted root/stage budgets and authorization; unresolved infrastructure or scope blockers stop automatic mutation.
