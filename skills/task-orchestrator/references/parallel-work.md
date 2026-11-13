# Optional native parallel work

Use when at least two independent ready parts benefit from available, permitted native subagents: focused research, disjoint implementation proposals or read-only acceptance/review investigations. Use serial work for trivial/dependent tasks or unavailable native tools. Optional native absence alone is no blocker; no parallel SDK or MCP service is required.

Keep one lead coordinator. Before dispatch, give each agent a bounded brief with these required fields:

| Field | Contract |
| --- | --- |
| Goal and scope | Approved parent outcome/stage and the exact bounded subtask |
| Ownership | Read paths, exclusive write paths, workspace or proposal-only mode |
| Inputs | Original requirements, current source/evidence references and versions |
| Output and acceptance | Structured status/result, evidence references, proposed or actual changed paths, blockers/limitations and observable success conditions |
| Dependencies | Prerequisites, integration owner and when the result may be used |

Maintain a dependency graph rather than a fixed agent batch. After every result, failure, new dependency or changed capacity, recompute the ready set. Dispatch only tasks whose prerequisites are satisfied, bounded by currently available host/provider slots, permissions, resources and the remaining approved budget. Queue excess ready work; do not invent capacity or hardcode one provider's current agent limit. Scale down or up within the approved scope without another user interview. A failed prerequisite holds its dependents while independent work can continue.

A graph may reveal **1 → 4 → 1 → 5** logical ready sets: one prerequisite, four investigations, one integration barrier, then five follow-ups. These are not fixed process counts. Queue excess work until permitted slots free; recompute readiness and release follow-ups after their barrier succeeds.

For mutations use separate authorized workspaces with exclusive write ownership, or have agents return proposed edits for the lead to apply. Shared-root runtime mutations remain serialized even for disjoint filenames. Preserve root execution leases, CAS, operation reconciliation and receipt bindings; do not launch conflicting `ase run`/check/verify calls or bypass a root lock. Integrate at dependency barriers and run final affected tests and independent reviews against the combined current result.

Reuse original-assignment authorization, persisted recovery history for each underlying problem and any explicit root/stage limits. Decomposition, aliases, additional agents, new waves and session changes cannot reset unsuccessful-cycle/diagnostic/probe history or create external authority. A delegate's new prose is not verified progress. New material outcome, serious architecture, resource, cost/risk or authorization changes still follow questions-and-approval.

Native advisory answers help investigation and integration. They are not formal acceptance/code-review evidence unless a genuine independent Runner execution captured the required receipt for the current source/contract/plan/check bindings. Optional native delegation and formal independent verification are separate capabilities; serial fallback does not waive mandatory verification.

Pattern basis (primary sources, checked 2026-11-12): [Anthropic](https://www.anthropic.com/research/building-effective-agents) describes dynamic workers and synthesis; [OpenAI](https://developers.openai.com/api/docs/guides/agents/orchestration) retains a manager's reply ownership with bounded specialists; [LangGraph Send](https://docs.langchain.com/oss/python/langgraph/graph-api#send) supports runtime-sized fan-out and explicit shared-state reduction; [GitHub](https://docs.github.com/en/copilot/concepts/agent-capabilities/choosing-and-delegating-agent-work/delegating-and-parallelizing-work-with-agents) covers dependencies, context, integration and coordination costs. Local design inference: use a lightweight ready queue and native capabilities without adding those frameworks as dependencies.
