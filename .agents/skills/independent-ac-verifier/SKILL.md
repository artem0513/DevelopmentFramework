---
name: independent-ac-verifier
description: "Use when a managed task needs a separate acceptance judgment against the original request, current contract and actual profile-specific evidence."
---

# Independent acceptance

Apply relevant [evidence decisions](../task-orchestrator/references/evidence-decisions.md): actual target/reference authority, required dependency postconditions and current artifact purpose/version. For migration/optimization assess meaningful equivalence and relevant boundaries/preexisting ambiguity. Metadata or prepared drafts are not consumer/execution evidence. Missing relevant code is a coverage gap to investigate or disclose; it does not establish absent behavior.

Act in a separate session from implementation. Read the original request and sources, approved proposal/stages, acceptance obligations, current artifacts and actual executed evidence. Inspect the delivered result directly; an implementation summary is only a locator.

For each criterion report its ID, expected behavior, source/evidence references, verification method, PASS/FAIL/UNVERIFIED/BLOCKED/NOT_APPLICABLE, limitations and missing coverage. Mandatory unexecuted checks cannot PASS. Do not weaken acceptance to fit the implementation or infer a real resource/document/model run from a fixture.

For configured software test adequacy, inspect the separate scenario/consumer-boundary assessment and its missing coverage. Independently identify materially omitted risks; positive counts or an adequate verdict alone do not establish original behavior. An ordinary AC receipt cannot substitute for the distinct adequacy execution.

Use missing_coverage only for unmet proof required by this configured criterion at this gate. Any nonempty value blocks a mandatory criterion even if status says PASS. Put nonblocking coverage limits and mandatory later delivery inputs/effects in limitations and the pending delivery plan; they do not become prerequisites for current source approval. A real current evidence gap remains UNVERIFIED/BLOCKED. Passing a source criterion never establishes later publication or overall completion.

Apply task-orchestrator's [profile evidence](../task-orchestrator/references/profile-evidence.md). Software retains mandatory successful commands, positive actual test counts, no skipped required tests and current source/contract/plan fingerprints. Documents, research and infrastructure need substantive independent evaluation of their own deliverables. Suggest AC-derived checks independently of implementation assumptions.

Use `ase --project PROJECT verify TASK` through the authorized local backend adapter to capture independent receipts. A missing optional integration is not a prerequisite; unavailable independent-session capability is an explicit blocker. Do not manufacture receipts, copy credentials or relax permissions. Separate model judgment does not establish a separate OS security identity.
