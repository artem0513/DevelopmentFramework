# Proportionate solution design

Use before choosing how to implement a software or infrastructure change. Build the smallest
change that meets the ticket's acceptance criteria and fits the project's existing patterns, the
way a careful developer on this team would. Correct code with unrequested machinery is a failed
delivery: the team has to remove it. A workaround that avoids touching the right code is not
"small" either. Scale the steps below to the task: one line each for a trivial change. See the
[calibration examples](solution-calibration.md).

## 0. Understand the problem first; code comes after

The first deliverable is a short problem analysis, before any code:

- **What happens now**, reproduced or traced in the code: the actual flow and data.
- **Where the system already meets the condition**: the existing point that detects or handles
  it (the place where using a token fails, the status transition, the query that selects items).
- **The smallest hook point** for the change.
- **The ticket class**: code change, investigation/clarification, or both.
- **For a defect**, its scope: every entry point with the same root cause and any earlier
  compensations for it.

For a bug, first check that the behaviour is really a defect. When it is a question of
expectations (rows that differ by version, data the reporter misread), the deliverable is a
ticket comment or question to the reporter, not code. Write no code until the analysis exists
and matches the ticket.

## 1. Understand the system before deciding

- **Business need.** Restate what the requester needs to happen, not only the wording.
- **Scoped system map** (C4, only the part the change touches): dependent apps/users;
  services/apps, how they exchange data and **who owns each contract**; components and the
  existing path for this kind of work; the files to change and their size. Use code, config,
  docs/ADRs, Git history and PR/issue discussion; mark what is unverified.
- **Reuse first.** Before adding any mechanism, search for the project's existing one
  (email/notification command, job, transaction pattern, validation, caching, auth check) and use
  it. Record what you searched and found.
- **Precedent.** Follow the team's established way unless evidence shows it no longer fits. On a
  codebase new to you or the team, match the existing code style and change size over general best
  practice, and apply this guide strictly.
- **Root cause.** For wrong or missing data, trace caller -> boundary -> callee to the first place
  the value becomes wrong.

## 2. Triage every risk; only required risks become code

For each risk scenario you can think of (race, transient failure, stale data, duplicates,
concurrent edits), classify it:

- **(a) Required** -> code and test it. A risk is required when:
  - the ticket, an acceptance criterion or the reviewer asks for it; or
  - without it, the change itself would break the project's existing authorization, validation
    or error conventions on new paths, existing invariants and contracts, lose or corrupt data,
    or duplicate external or financial effects (with a concrete counterexample).
- **(b) Already handled** by existing behaviour (a one-time state transition, an existing
  transaction, a later re-check) -> note where.
- **(c) Unlikely or out of scope** -> do not code it; list it under "Not handled" in the PR with
  the reason ("not required by AC-x; no observed trigger").

"More robust" is not a requirement. A theoretical race is (c) unless the ticket or evidence makes
it real.

## 3. Hook into existing flows; mechanisms need a reason

Prefer the place where the system already encounters the condition. Add no new scheduled job,
trigger, pipeline, polling, or global/root UI component (app shell, root component, global
layout) unless the ticket explicitly requires it.

Locks (local or distributed), new or conditional transactions, atomic claims, retries, queues,
caches, polling, version/incident IDs, new columns/tables/migrations, and optional or nullable DI
dependencies are added only when:

- a category (a) risk needs them, or
- the project already uses that exact mechanism for that exact flow.

Otherwise leave the risk documented and unhandled, or ask the owner. Do not wrap new code in
defensive re-checks of state the existing code already guarantees.

Whether an addition beyond the ticket is "really needed" is not your call. Keep it only as an
explicit question to the developer or lead, with its reason; never keep it silently.

## 4. Fix at the source, for every consumer

- Fix the component that produces the wrong value. When the confirmed root cause is in shared
  code (DB context mapping, middleware, a shared handler), fixing it there is in scope; declare it
  as an effect with its blast radius.
- Code in another repository (another team's service) is changed through its own managed task or
  child task, with its own baseline, gate, review and publication authorization. Prepare that
  change; the other team's code review is not an owner approval and does not hold your work.
- A consumer-side workaround (mapper, fallback, re-computation, retry, cache) only when the source
  is outside your access or authority (external vendor, frozen system): put it at the integration
  boundary, mark it with the upstream reference and removal condition, and tell the owner.
- After fixing a source, find other consumers of the same fault and earlier compensations; fix or
  remove them, or record why not.
- When the same fault exists at several entry points, fix every one in this change. Use a shared
  handler/mapping when one already exists for that purpose; otherwise apply the same small fix at
  each entry point and offer the shared version as a separate ticket. These hunks belong to the
  defect, not to "unrelated changes".

## 5. Choose a proportionate form

State the chosen form, why not simpler and why not more complex.

| Situation | Proportionate form | Wrong in either direction |
|---|---|---|
| A few lines in a large file | Edit those lines | Refactoring "while here" |
| Substantial new logic for a large file | New classes/files (folder, or a project in the solution when large), called from the existing code | Growing the large file further |
| The task changes most of a component's logic | Restructure that part, tests first, risk stated | Patching around it |
| The ticket says how to do it ("do not refactor") | Follow it; note any risk | Overriding the instruction |
| Data available through one simple join/navigation | Read it there | A duplicate column |
| Data required by the ticket and not stored anywhere | A new column with justification | Heuristics, log parsing, heavy procedures |
| A new entity with its own lifecycle | A new table (infrastructure, see 6) | Overloading unrelated tables |
| A monolith needs a capability | A module/project in the solution, reusing existing services | A new service, function or queue |
| Several services need a capability none provides | A dedicated component/service (infrastructure, see 6) | Copying it into each service |
| A policy value changes | Change the value and its test | Config systems, date switches, flags |

## 6. Who decides

- **You decide** (justify in the proposal): code in any number of services in this workspace, new
  files/folders/projects inside a solution, refactoring the task warrants, root-cause fixes in
  shared code, and `kind: "schema"` changes - a column, index or constraint migration a requirement
  needs - and contract changes between your services with consumers updated.
- **Incidental shared-code changes** - hardening, claim logic or other edits to job runners,
  schedulers, publishers, DB context, DI setup or middleware that the root cause does not need -
  stay out of a feature ticket. Paths listed in `.devframework/protected-paths.txt` additionally
  block the gate until `ase authorize-protected` records the owner's permission; ask for it in the
  approval format below and build no workaround meanwhile.
- **The owner approves before you build** any added or removed infrastructure (`kind:
  "infrastructure"`):
  - a service or application;
  - a database or a table;
  - an Azure Function or other serverless unit;
  - any timer, scheduler or background job, including an in-process hosted service;
  - a queue, topic or subscription (a new message type on an existing queue is code);
  - an external platform or provider;
  - a new cloud resource.

  DB functions, procedures and triggers count as infrastructure unless the project already uses
  them for that kind of logic. Declare each with basis `ASSUMPTION` on a blocking unknown; only the
  owner's answer resolves it, never a retrievable fact (`DECISION` once answered). A source ref
  counts only when the owner's own text names that infrastructure: that is pre-authorisation;
  report it afterwards. The CLI checks only the binding; reviewers check that the record names the
  infrastructure. Do not build it before approval.
- **The approval request** gives:
  - the business need and the evidence;
  - the options from simplest up (for example lazy expiry on access, an existing job, sending from
    each service), and why each is or is not enough;
  - the recommendation and its impact on other services, operations and cost;
  - one decision to make.
- **Do not ask** about what code, docs or history answer, internal design choices, which team
  reviews code, or release timing for a change you can prepare now. When the ticket implies a date
  ("from next month"), state the date you read and proceed; put it in the release note instead of
  holding the change.

## 7. Developer ownership and human-led mode

- Give a short walkthrough the developer can explain line by line: why this place, why this size,
  what was deliberately left out.
- **Human-led mode** is on only when the task explicitly asks for it, or when AGENTS.md contains the
  exact line `DevelopmentFramework mode: human-led` (then for the whole project). Nothing else
  enables it. In this mode the developer writes the production code. DF:
  - gives the analysis, hook point, step plan and test proposals;
  - writes tests only on request;
  - independently reviews the developer's diff and polishes text.

  The task runs under the research/review profile, without an implementation gate.

## 8. One ticket, proportionate tests, plain PR

- One ticket per PR. An unrelated bug found on the way goes to a note or a separate branch/PR.
- Tests cover the acceptance criteria and category (a) risks, written, not promised; prove the
  original failure through the real consumer path where practical. When no test project covers the
  changed layer, add the smallest one to the solution (your call) instead of leaving a test plan.
  No tests for invented races, and no test volume several times larger than the change.
- Required side effects (messages, payments) avoid duplicates on retry and keep related writes in
  one existing transaction where the context allows (an instance of 2(a)). Prefer a reliable signal
  over a heuristic.
- PR description: short and plain - what the ticket asked, what changed, what was deliberately not
  handled, how it was verified. No showcase of mechanisms.

Independent review checks proportionality as a blocking criterion: unrequired mechanisms,
incidental shared-code edits, bundled unrelated changes, avoidable workarounds and unapproved
infrastructure are findings. A diff far larger than the minimal sketch triggers that scrutiny; the
finding names the specific unrequired hunks.
