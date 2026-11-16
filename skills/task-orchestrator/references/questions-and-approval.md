# Questions and approval

Before reporting a blocker, apply relevant [evidence decisions](evidence-decisions.md): required outcome/affected work, concrete failed check, bounded conclusion, useful authorized alternatives or stopping reason. A failed optional method is not itself a required-outcome blocker. Keep purpose/version explicit for draft or reference artifacts.

Use this reference only for a material unresolved decision or a changed authorization boundary.

A clear assignment authorizes its in-scope execution immediately. Record a concise internal proposal and original authorization; do not require another approval of the same work. Choose ordinary internal details and technical taste using project rules and suitable colleague examples. A question is necessary for a genuine unresolved business/access/authority blocker or serious architectural change beyond the approved scope: relevant [answer investigation](../../task-discovery/references/answer-investigation.md) cannot establish the answer, or only the owner can decide; it materially changes outcome, compatibility, cost, risk or authority; and no safe in-scope choice is supported. Existing behavior does not establish a future business decision.

For retrievable facts, a question needs actual inspected evidence or a concrete access/scope limitation, plus a stopping reason: no useful remaining authorized check can resolve the material gap. Missing text in the assignment, an untried tool, a planned command or one denied optional method is insufficient. Check supplied/fresh context first, then the relevant code/history, logs, data or environment as appropriate. Do not delay a genuinely owner-only decision with irrelevant research; do not invent a plausible answer merely to avoid asking.

Send a roughly 2–4-line owner message with these parts, in order:

1. The relevant evidence actually inspected and what it establishes; label unavailable evidence.
2. The remaining material uncertainty and why further authorized checks cannot settle it.
3. The hypothesis about the owner's intent, explicitly unconfirmed.
4. The recommended solution and why it fits; keep it distinct from the intent hypothesis.
5. One concrete decision requested, including a material alternative's consequence when useful.

For example: "Partner fee ownership remains undecided. The current contract and fresh meeting notes cover domestic net payouts only. My unconfirmed reading is that you intend gross partner payouts. I recommend gross because it leaves the fee with us and avoids an unapproved partner deduction. Should partner payouts be gross or net?" Use only evidence actually inspected. Routine filenames, implementation order or an optional missing example do not need this message.

Hold only explicitly affected known stages and their dependents; absent or ambiguous impact mapping stays conservative. Continue already approved independent work. After the owner answers, record the decision, refresh affected assumptions/contracts/evidence and re-evaluate remaining gaps. An answer does not authorize unrelated changes or invalidate unchanged independent approval.

Route remaining material questions to the task owner first. If team input is needed, prepare a concrete question for owner review and send only after explicit approval of that question and destination. Standing channel/routine-status permission does not bypass this review for unresolved task questions. Reuse approval of an already approved concrete question and destination. An internal draft is reversible task preparation and needs no separate approval.

The internal proposal is concise: result; approach/reason; technical means/reuse; main steps; verification; material assumptions. Keep the detailed contract separately inspectable. A progress update can state the intended action without asking the owner to reapprove a clear assignment. Do not hide a material condition to meet the owner-message line target.

Record task ID, contract revision, exact proposal hash, approved stages, user-message reference, UTC approval time and external-action bounds through `ase task propose` / `ase task approve` (global project options precede the subcommand). The record accounts for an observed user authorization; inventing a reference does not create permission. Reuse authorization already present in the conversation for the same outcome and scope. Do not ask again for a technical correction that preserves it.

Require new approval when business meaning, serious architecture, material risk/cost, accessed resources or external authority expand beyond the assignment. Internal stage decomposition must remain a subset of an approved parent stage and retain its obligations. Execution-plan revisions invalidate affected receipts without automatically revoking business approval. Platform permissions remain in force.
