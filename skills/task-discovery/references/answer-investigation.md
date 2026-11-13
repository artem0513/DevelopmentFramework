# Investigate before asking

Use for an unresolved fact or conflicting requirement that could materially affect the task. This is a question-driven investigation, not an inventory of the whole environment. Obvious contradictions in the request or a decision only the owner can make can go directly to the owner; do not delay them with irrelevant searches.

## Choose evidence for the question

Check the assignment for obvious gaps first. State the uncertainty, the hypothesis about intended behavior and what observation would distinguish plausible alternatives. Restore fresh relevant project/meeting knowledge before repeating research. Start with the smallest authorized check likely to change the decision; record actual observations separately from the intent hypothesis, recommended solution and proposed next checks. Re-evaluate the remaining gaps after each material finding or owner answer.

Choose relevant sources rather than completing a checklist:

- Current target code, tests, configuration, contracts and applicable instructions establish implemented behavior and explicit constraints.
- Targeted Git history, issue/PR discussion and ADRs can explain why a behavior exists. Verify that a historical decision still applies; commit messages and one colleague's implementation do not establish current business authority.
- Bounded incident logs/traces can distinguish runtime hypotheses. Use relevant IDs and time windows; avoid collecting unrelated records or secrets.
- Database schema and migrations establish structure; a bounded, authorized read-only query can test a data hypothesis. Schema does not establish actual rows, and observed rows do not decide future policy. Do not dump the database, mutate data or change access controls to answer a question.
- Relevant shared components or sibling services can reveal contracts, conventions and their rationale. Respect the [example-search opt-out](project-patterns.md): no analogue search/reuse via history, cache or delegation after an opt-out. Necessary actual dependency/contract research remains permitted.

Source availability is not access authorization. Use existing authorized CLI/API/file tools without requiring MCP; access failures are observations, not permission to expand access. Do not install an integration or contact a team merely to complete this list. If retrieved evidence conflicts, investigate the relevant contradiction instead of counting agreeing sources. Existing behavior can inform a recommendation; it cannot authorize a new business policy.

## Continue useful investigation

There is no default one-pass/one-follow-up cap and no default research cost/time budget. Respect an explicit user/project/task bound already in force. Continue with a small coherent set of discriminating checks when actual new information, contradictory evidence or a credible lead gives the next check a useful prospect of resolving the uncertainty. Follow a precise trace, dependency or rationale reference even after earlier passes. Rephrasing the same search or producing more notes without new information does not justify continuing.

Keep the investigation history in the existing root-task checkpoint/notes. Resume, delegates and related-question rewording share inspected evidence, remaining leads and any explicit limits. Research does not expand approved scope or access. Its information gain is distinct from verified implementation progress under the recovery policy: learning a plausible cause is not itself a satisfied acceptance obligation.

Stop as soon as any of these holds:

- Current evidence sufficiently supports the required decision; further source categories need not be visited.
- The next targeted check has no useful prospect of resolving the remaining uncertainty, including repeated results without a new lead.
- Relevant sources within the inspected scope are exhausted, or required access is unavailable and no authorized alternative can resolve the gap.
- An explicit agreed investigation/time/cost bound is reached, or the user requests a stop.
- The remaining issue is an unmade business decision that existing evidence cannot settle.

Stopping investigation does not automatically mean asking a question. Choose a safe in-scope default yourself when supported by current project rules, contracts or delegated discretion. A lower-risk hypothesis remains a recommendation; it cannot silently create a business commitment. Otherwise use the [material-question rule](../../task-orchestrator/references/questions-and-approval.md), holding affected stages/dependents while independently approved work continues. An optional unavailable source alone does not block that work. Never describe a denied/unread source as inspected or an explicit reached limit as proof that no answer exists anywhere.

## Preserve a concise outcome

Keep the unresolved question, inspected source locators/current version/time window, observations, intent hypothesis, recommendation and reason, remaining unknowns, next useful lead or stopping reason, and effort actually used. Mark unavailable/uninspected sources and explicit limits. Update existing project knowledge/checkpoint and dated journal without storing raw logs, rows, conversations or secrets. Refresh affected conclusions when their sources or an owner answer change; a previously exhausted search is not permanent proof of absence.

Use a roughly 4–6-line owner message: remaining uncertainty; checked evidence; intent hypothesis labeled unconfirmed; recommended solution and why; one concrete decision. Keep the hypothesis distinct from the recommendation. For team input, route the concrete question through the owner under the questions-and-approval workflow.
