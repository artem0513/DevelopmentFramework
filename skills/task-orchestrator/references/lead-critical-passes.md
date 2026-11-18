# Three additional passes by the original main bot

After initial implementation and meaningful executed checks, the original main
chat must reconsider the whole solution three additional times for configured
nontrivial root tasks. Do not wait for the user to request edge cases. Workers
may investigate bounded questions; their reviews do not count as main-bot passes.
Independent acceptance, substantive review and software adequacy remain separate.

## Applicability and requirement authority

New CLI tasks carry `lead_review: {version:1, required:true, additional_passes:3}`.
Programmatic creators carry the explicit policy. Saved unconfigured contracts
stay historical; never fabricate old passes or silently rewrite their policy.
The requirement applies to final root completion in every supported domain.
Children/intermediate stages keep their own checks without another root barrier.

Default uncertain complexity to nontrivial. For genuinely mechanical LOW-risk
single-stage work, `lead-review classify` needs the concrete reason and current
passing checks/source. No actions, access, unresolved discovery/requirement gaps,
required children or mixed work. Reassess changed input. Independent AC must
explicitly agree with the exemption; a cheap implementation is not triviality.
Once a substantive critique is credited, the earlier mechanical classification
is historical: it cannot bypass recorded findings or the required review depth.

Every pass reads the original request and full approved contract/proposal
history from `prepare`. Include approved owner clarifications and label explicit
supersessions. Current code, implementation summaries and generated tests cannot
redefine expectations. Assess every mandatory criterion and original/latest
request using its provided requirement hash and concrete source/check evidence.
Missing relevant facts are UNVERIFIED, not invented business policies or bugs.

## The three passes

All three inspect the complete task; their principal focuses are:

1. Requirements/business scenarios: omitted constraints, contradictions,
   unsupported assumptions and consistency with approved clarifications.
2. Attempts to break the result: boundaries, invalid inputs, dependency errors,
   repeated/concurrent effects, recovery, relevant security and performance.
   Performance conclusions need measurements. Justify inapplicable areas.
3. Whole-solution requirement reconciliation after fixes: interactions, actual
   consumer behavior, meaningful assertions and regression coverage. Check every
   original/current obligation again.

Zero findings is legitimate. A finding identifies a requirement, concrete
violation/counterexample, source and executed evidence. Investigate suspicions;
do not mutate for speculative defects, preferences or invented requirements.
Where useful, check that tests detect a relevant deliberately broken behavior;
passing counts alone do not establish fault detection.

**Record the assessment before fixes.** Repair confirmed in-scope findings under
existing authority, rerun affected checks/regressions, then perform the next
pass on current facts. If input changed before capture, retain the original
response as stale, settle its ticket and prepare a current critique. Earlier
valid passes survive later fixes, but the final critique must be current. A fix
after pass three requires a corrective follow-up. Explicitly resolve earlier
finding IDs; omitting them cannot close them.

A new approved requirement anchor starts its own three-pass depth. It does not
close old material findings, even when a criterion is renamed or removed. The
host replays authenticated prior series, including old databases without carry
fields. `prepare.finding_ledger` identifies each finding's original anchor.
New response schemas require `origin_anchor_sha256` in resolutions: use an empty
string for this series or the exact inherited origin for an older finding.
IDs may be reused in another series without conflating their obligations.
Legacy responses remain local to their original series. A `fixed` disposition
needs changed evidence; `not_a_bug` needs counterproof under the original rule
or a concrete explicit saved owner-approved supersession, challenged by AC.
Old open findings also block a later trivial exemption. Original inputs for
inherited obligations remain available beside current passes; old passes never
count toward current depth. Missing/tampered credited history blocks completion.

Three is initial checking depth, not a guarantee or a repair cap. Continue for
material gaps under existing progress-sensitive recovery. Three unsuccessful
repair cycles without verified progress trigger reassessment; another review
paragraph is not progress. Ask only for a genuine remaining decision/access/scope
blocker. Routine fixes and further supported checks do not need another approval.

## Native host and local CLI

Bind the original chat with `events bind` using actual available backend/thread/
host identity. If native identity is unavailable, use explicit `events bind-local`
and its local owner. Never invent an ID, replace the lead or start a new lead per
pass. In project-local mode use that project's installed launcher.

```text
ase lead-review status TASK
ase lead-review prepare TASK --thread-id ORIGINAL --expected-revision N
# Perform the returned critique in that same original main chat.
# Save the assessment and host-observed origin/unique native turn reference.
ase lead-review record TASK --attempt ATTEMPT --thread-id ORIGINAL --expected-revision M --file result.json
ase lead-review recover TASK
```

`prepare` commits CAS intent before native work. `record` seals the already
observed response before its SQL projection. On interruption inspect the exact
ticket, native output and saved captures through status/recover; do not resend a
prompt or start a replacement. Complete saved output recovers without a model
call; partial original bytes remain for reconciliation. Native origins are
cooperative host attestations, not independent CLI receipts or OS isolation.

Each ticket seals its prepared source hashes and complete check receipts. After
the response is captured, referenced source bodies and associated executed logs
are sealed before SQL credit. Recovery reuses the exact response and sealed
inputs. If bytes changed before they could be saved, keep the response historical
and perform a current follow-up; never label today's code as an earlier input.

Whole-root packets include verified historical bodies once by content hash. Read
each pass's `inputs` mapping in `_ase_context/lead_review.json`: `packet_path`
can name either an identical current file or `_ase_lead_history/<hash>.txt`.
Compare earlier findings with their original inputs and current corrections.
For a reference with `encoding: line_delta_v1`, `packet_path` instead names a
JSON recipe; verify that file against `representation_sha256`. Its base is a
complete current file in this same packet, identified by `base_packet_path` and
`base_sha256`. Split that base with `splitlines(keepends=True)`. In order, copy
the unchanged base lines before each `start`, append that edit's `content`, and
skip the original base lines through `until` (exclusive); append the remainder.
All indexes refer to the original base. Verify the restored UTF-8 bytes against
the reference's `sha256` and recipe's `source_sha256` before using them as old
evidence. This is read-only reconstruction, never an edit to the current project.
Recipes cannot chain; source size, line/edit counts, ordered ranges and exact
reconstruction are checked by the host. Unhelpful deltas remain raw. The durable
archives retain every original byte; a still oversized packet remains blocked.
An approved-intent `contract` may reference the complete identical
`_ase_context/contract.json` already in that packet via `$ref` and its UTF-8
SHA256. Restore that decoded object before checking original intent/approval
hashes; differing historical contracts remain complete. Stored intent and
`prepare` still supply the original complete data.
A `/scope` reference may also list `exclude_resources`: remove only those
resources in the existing order, then verify `value_sha256` of the restored
canonical JSON. This reconstructs an exact older scope; it never removes inputs
from the current task or grants authority. Different remaining fields stay full.
When identical optional-source notes are shared in coverage, that exact note
applies to every omitted-source row; all individual policies and gaps remain.
Older passes can use only an exactly bound, authenticated archived Runner request;
its original verdict remains historical and is not a new acceptance. Missing,
tampered or oversized input coverage is explicit and prevents verified completion.
Do not ask the owner to reconstruct retrievable local evidence or silently drop
an earlier finding to obtain approval. Stage packets avoid the final-root barrier.

`awaiting_lead_review` from run/resume is an internal continuation. The main bot
performs its next pass; it does not ask for approval, stop with a user blocker or
repeat successful tests/independent calls just to await itself. After current
passes, obtain final independent verification. Whole-root receipts separately
bind the lead-assessment digest; stage/action context remains reusable. These
passes never replace independent AC/review/adequacy.

For remote or non-file work, use the exact `check_sources[check_id]` returned in
the prepared base: it names the captured log (or actual check record) included in
the packet. A URL supplied by the owner is scope, not proof of observed state.
Closing a finding as fixed requires a changed source, plan, action proof or
parsed observed outcome. New timestamps from rerunning identical checks do not
establish a fix; independent acceptance still checks the actual correction.
Approved proposals are retained in full, so use credential references and never
put passwords/tokens in proposal fields or saved requirement text.
