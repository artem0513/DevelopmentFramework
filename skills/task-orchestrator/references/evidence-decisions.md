# Evidence, readiness and decisions

Use relevant sections for comparisons, blockers, dependencies, actionable artifacts, semantic changes or review findings. This refines the existing workflow; it adds no universal form, new approval or mandatory service. Clear authorized work proceeds. Use the [evidence brief](../assets/evidence-brief.md) when several material facts must stay aligned; otherwise retain them in existing notes.

## 1. Establish the target and reference

Identify the actual resource and relevant environment, tenant/account and consumer. For a comparison or migration, identify the reference's role and why it is authoritative for the requested result: an applicable owner decision, contract or verified provenance. Record its locator and current version. Deployed configuration proves what is used; availability, similar names and a colleague's implementation do not prove the right business baseline.

Separate inherited behavior/settings from changes introduced by this task. Reuse compatible project conventions; avoid an override solely because an inherited value differs from a guess. Investigate a material conflict with the approved target. Escalate an unresolved owner-only choice using the existing short question format. An unconfirmed reference does not authorize changing the target or unrelated systems.

## 2. Bound the claim and prove the blocker

A blocking claim names the required outcome, affected stage/dependents, and actual observation: operation, target, safe account/access-method reference, time/version, result and inspected scope. Keep credentials and raw responses out of notes. Distinguish a known failure from an unperformed check.

"Object not found in schema S with account A" is bounded evidence. "The object does not exist" needs corresponding coverage. One denied login or failed optional adapter does not prove the application is broken or all access is impossible. Try a concrete useful authorized alternative when available; an optional integration failure does not block usable CLI/file work. Do not request new access or change permissions silently.

Continue research while a specific lead can distinguish hypotheses. Stop on sufficient evidence, no useful lead, unavailable required access, an owner-only decision or an explicit stop/limit. Record the stopping reason and remaining unknowns; exhausted inspected sources are not absence everywhere. Then proceed with a supported in-scope choice or ask one material question: checked facts, unconfirmed hypothesis, recommendation/reason and decision needed. Hold affected work and continue independent approved work. Reconcile UNKNOWN external effects rather than replaying them.

## 3. Check the readiness the obligation actually needs

These are separate observations, not an automatic promotion ladder:

| Observation | What it establishes |
| --- | --- |
| Promised/announced | Delivery is unverified. |
| Exists/visible in metadata | The object/configuration is visible in the inspected scope. |
| Usable by intended account | The required authorized operation actually succeeds through the relevant access path. |
| Structurally complete | Required fields, interfaces and version are present. |
| Data suitable for this task | Bounded actual data/postconditions meet the task requirements. |
| Consumer behavior verified | A meaningful consumer-level check demonstrates required behavior. |

Record only the levels needed for the affected obligation, with evidence and unknowns. Installed tools are not usable sessions. Metadata is not a successful SELECT. Readable empty data is valid when the contract allows emptiness; it is not proof of representative data or correct calculations. Probe the relevant intended account/consumer within existing authority; do not broaden access or copy secrets. Once required postconditions are evidenced, proceed without a ceremonial checklist or new approval.

## 4. Separate artifact purpose, state and version

For an artifact that may be executed, handed off or used as evidence, retain:

- Purpose: discussion draft, reference/example, or intended execution/delivery.
- State: draft, prepared, applied/delivered, or independently verified, supported by actual evidence.
- Identity: path/object, content hash or meaningful version, target and relevant requirement/dependency source versions.
- Unresolved placeholders/assumptions and the current next action.

A reference CREATE script for an existing object is not a command to execute. Prepared text is not an applied resource. A filename such as final.sql is not readiness evidence. Before authorized execution or execution-ready handoff, inspect exact current bytes/version, target, required inputs and unresolved placeholders. A legitimate runtime parameter is not an unresolved placeholder. Check copied text for meaningful encoding/invisible-character corruption; do not strip whitespace or rewrite strings/identifiers blindly. Reuse authorization when its scope still applies; preparation/verification grants no external action by itself.

After a material answer or changed source, identify affected assumptions, artifact versions, acceptance checks and cached conclusions. Refresh these and their dependents, mark old versions historical/superseded, and update the current brief before the next action. Keep unaffected work valid and preserve evidence of already-applied effects; refreshing a draft does not authorize replay. Use existing intake/checkpoint/knowledge fields and source references. The brief is an optional view, not a new schema or second tracker.

## 5. Verify meaning when changing implementation

For migration, translation or optimization, identify required preserved behavior and its authority. Compare old/reference and proposed behavior using relevant examples: normal/boundary cases, null/missing values, duplicates, errors, ordering, rounding/units/time windows or permission effects as applicable. Use meaningful differential checks and the actual consumer contract. HTTP success, matching columns or one happy-path row alone do not prove equivalence.

A documented platform capability is a hypothesis about this execution path until checked. A narrowing filter needs evidence that excluded data cannot affect the required result. Separate a new regression from ambiguity already in the reference. Preserve supported behavior within scope; do not silently invent a business rule to resolve ambiguity. Hold only a material unresolved choice and its dependents, with a concise recommendation; continue independent work.

## 6. Substantiate and respond to review

Before asserting an actionable defect, trace a concrete trigger through the relevant caller, callee and contract; cite the inspected location or observation supporting the consequence. Tests and actual error paths can establish whether a guard/transition exists. An omitted callee means missing coverage, not proof of absent behavior: obtain relevant code if authorized, or report a material gap as UNVERIFIED/BLOCKED without inventing a defect.

Before repair, verify the finding against current code/evidence. Reproduce or otherwise establish the actual consequence. Repair confirmed in-scope defects, then rerun affected checks and fresh independent AC/review. Reject a disproven finding with evidence and retain the finding/response in history. Avoid speculative changes or downgrading a real P0/P1/P2 to obtain approval. Existing scope, recovery and separate-session requirements remain in force.
