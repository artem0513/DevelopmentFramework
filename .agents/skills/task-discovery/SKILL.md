---
name: task-discovery
description: "Use when task-relevant environment, sources, local knowledge freshness, tools, skills or independent backend capabilities need inspection."
---

# Task discovery

Start with the named resources and applicable local instructions. Expand inspection only for a material dependency, a question relevant to the requested outcome, or a narrowly scoped search for relevant project examples; record the relationship and remaining uninspected dependencies. Reading a location does not adopt it or authorize product changes, installations or external effects.

Before asking the owner about an unresolved fact, use [answer investigation](references/answer-investigation.md): choose relevant authorized evidence, including available Jira issues and project wiki when relevant, check the likely explanation and its rationale, and stop when the answer is supported or the bounded investigation ends. Code alone does not establish business intent. Do not run every available source category for every question or search indefinitely for an unmade business decision.

Before proposing implementation, search for similar work in the authorized project by default, including relevant shared packages and sibling microservices. Follow the [project examples](references/project-patterns.md) workflow. Record one outcome: a source-backed example and intended reuse; no suitable example within the inspected scope; or search skipped by the user's explicit instruction. An unavailable external example does not replace this local search. A known, current, suitable example can satisfy the search without an exhaustive scan.

If the user explicitly says not to use templates/examples or not to search for analogues, skip deliberate example search and reuse for that task, including cached pattern retrieval and delegated searches. Continue necessary target/dependency inspection and applicable mandatory rules. Apply this choice before retrieving example records from memory; do not turn a task-specific opt-out into the next task's default.

Retrieve project context and relevant source-backed knowledge before rescanning. Check source versions and current observations: Git commit alone does not cover dirty/untracked bytes, external settings or non-Git workspaces. Refresh affected records and their transitive dependents; retain historical provenance and explicit unknowns. Memory is local to this computer and project identity, with no automatic cloud synchronization.

Read skill metadata and local resources first, loading bodies/references only for the active step. Choose one leading workflow plus applicable domain tools. For financial analysis or document production use domain capabilities and the corresponding acceptance profile; software checks apply to actual code/config changes. Do not install packages merely to enlarge the catalog.

Record a capability manifest separating executable/tool presence, usable version, permission, structured output, resource access and an actual independent session. Unknown is unavailable until probed. For Codex, Claude and Copilot use their local adapters where authorized. Protocol fixtures and native role files do not prove live execution. Missing independent verification remains an explicit limitation; an optional missing integration does not disable usable local CLI operations.

For task-relevant service authentication, inspect only safe `ase credentials list` metadata and the [credential access workflow](../task-orchestrator/references/local-credentials.md). Match the exact approved origin and intended account; a stored profile or recipe is not proof of live authentication. Do not scan credential files or provider homes for secrets. Resolve supported CLI configuration yourself within existing scope; unresolved account/environment ambiguity or required human interaction is a material blocker.

Check native subagent availability, permission and current capacity separately from formal Runner verification. When independent ready work can benefit, read task-orchestrator's [parallel work](../task-orchestrator/references/parallel-work.md), map prerequisites and ownership, and refresh the ready set as results arrive. Missing optional native delegation selects serial work; it does not by itself block the task.

Report sources inspected, reuse, capability gaps, justified expansions and proposal readiness. Resolve retrievable details before asking; use task-intake's material-blocker rule for remaining decisions.
