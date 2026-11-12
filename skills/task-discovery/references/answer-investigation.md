# Investigate before asking

Use for an unresolved fact or conflicting requirement that could materially affect the task. This is a question-driven investigation, not an inventory of the whole environment. Obvious contradictions in the request or a decision only the owner can make can go directly to the owner; do not delay them with irrelevant searches.

## Choose evidence for the question

State the uncertainty, the leading explanation and what observation would distinguish plausible alternatives. Retrieve relevant source-backed memory and validate its freshness before repeating work. Start with the smallest authorized check likely to change the decision; record actual observations separately from hypotheses and proposed next checks.

Choose relevant sources rather than completing a checklist:

- Current target code, tests, configuration, contracts and applicable instructions establish implemented behavior and explicit constraints.
- Available authorized Jira issues can supply the task description, acceptance criteria, linked issues and decision history in comments. Inspect relevant items before asking for facts they may already establish; distinguish approved requirements from suggestions, old decisions and unresolved discussion.
- The project wiki, including Confluence when used, can supply requirements, architecture decisions and operational runbooks. Follow relevant task/component links and check page version/update time, approval status and current applicability; a draft or newer page does not automatically override an approved contract.
- Targeted Git history, issue/PR discussion and ADRs can explain why a behavior exists. Verify that a historical decision still applies; commit messages and one colleague's implementation do not establish current business authority.
- Bounded incident logs/traces can distinguish runtime hypotheses. Use relevant IDs and time windows; avoid collecting unrelated records or secrets.
- Database schema and migrations establish structure; a bounded, authorized read-only query can test a data hypothesis. Schema does not establish actual rows, and observed rows do not decide future policy. Do not dump the database, mutate data or change access controls to answer a question.
- Relevant shared components or sibling services can reveal contracts, conventions and their rationale. Respect the [example-search opt-out](project-patterns.md): no analogue search/reuse via history, cache or delegation after an opt-out. Necessary actual dependency/contract research remains permitted.

Discover Jira/wiki pointers from the assignment, relevant project docs/configuration or fresh local knowledge. Start with exact issue/page IDs or links, then a bounded search within the authorized project/space for the unresolved question; follow useful related links without crawling the whole site. Use existing authorized read-only CLI/API/browser access or provided exports without requiring MCP or a new integration. If a source is absent or inaccessible, record that limit and continue useful permitted work; ask the owner only when the missing information is a material blocker. Preserve issue/page locators, version/update observations and concise decisions in existing local records rather than copying full tickets or pages.

Source availability is not access authorization. Use existing authorized CLI/API/file tools without requiring MCP; access failures are observations, not permission to expand access. Reading Jira/wiki does not authorize comments, issue updates or page edits. Do not install an integration or contact a team merely to complete this list. If retrieved evidence conflicts, investigate the relevant contradiction instead of counting agreeing sources. Existing behavior can inform a recommendation; it cannot authorize a new business policy.

## Bound the investigation

Set an effort bound proportionate to the question and existing task time/cost constraints. Unless a different bound is already agreed or recorded before starting, use one targeted pass and at most one follow-up for the same unresolved question or closely related question group. A pass is a small coherent set of discriminating checks, not a whole-project scan or an unlimited query loop. A follow-up needs a new hypothesis, contradictory evidence or a concrete lead with a useful next check. A synonym search that adds no new evidence is not a useful follow-up.

Track effort in the existing root-task checkpoint/notes. Share the bound across delegates, resume and related-question rewording; do not restart it by changing agents, stages or labels. This investigation allowance is separate from implementation repair-cycle limits. A different bound does not expand approved scope, access, time or cost, and reaching the limit does not grant an automatic extension.

Stop as soon as any of these holds:

- Current evidence sufficiently supports the required decision; further source categories need not be visited.
- The next targeted check has no useful prospect of resolving the remaining uncertainty, including repeated results without a new lead.
- Relevant sources within the inspected scope are exhausted, or the required source is unavailable under current access.
- The agreed investigation bound is reached.
- The remaining issue is an unmade business decision that existing evidence cannot settle.

Stopping investigation does not automatically mean asking a question. Choose a safe in-scope default yourself when one is supported by current project rules, contracts or explicitly delegated discretion. A lower-risk hypothesis is a recommendation, not an approved default merely because its alternatives are riskier; do not silently create a new business commitment. Otherwise use the [material-question rule](../../task-orchestrator/references/questions-and-approval.md), and continue already approved independent work while the dependent part waits. A remaining authorized alternative may still be worth a check within the bound if the preferred source is unavailable. Never describe a denied/unread source as inspected or a reached limit as proof that no answer exists anywhere.

## Preserve a concise outcome

Keep the unresolved question, inspected source locators and current version/time window, observations versus inference, recommendation and reason, remaining unknowns, stopping reason and passes/budget used. Mark unavailable and uninspected sources explicitly; record a useful next lead if work stops at the bound. Update the existing project knowledge/checkpoint and dated journal when relevant, without storing raw logs, rows, conversations or secrets. Refresh affected conclusions when their sources change; a previously exhausted search is not permanent proof of absence.

For an owner question use a short sequence: what was checked and found; what remains material; recommended option and why; one concrete decision requested. If team input is needed, route it through the owner under the questions-and-approval workflow.
