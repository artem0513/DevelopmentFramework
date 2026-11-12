# Questions and approval

Use this reference only for a material unresolved decision or a changed authorization boundary.

A question is necessary when all three hold: the answer cannot be retrieved reliably from authorized sources; it materially changes outcome, compatibility, cost, risk or authority; there is no safe choice within the approved scope. State the recommended interpretation or solution, its reason and the consequence of the alternative before the question. Continue independent permitted work while waiting. Choose ordinary internal details yourself.

The proposal is about 5–8 lines: result; business approach and reason; technical means and reused capabilities; 2–4 steps; verification; material assumptions and limits. Keep the detailed contract separately inspectable. Do not hide a material risk to meet a line count.

Record task ID, contract revision, exact proposal hash, approved stages, user-message reference, UTC approval time and external-action bounds through `ase task propose` / `ase task approve` (global project options precede the subcommand). The record accounts for an observed user authorization; inventing a reference does not create permission. Reuse authorization already present in the conversation for the same outcome and scope. Do not ask again for a technical correction that preserves it.

Require new approval when business meaning, material risk/cost, accessed resources or external authority change. Internal stage decomposition must be a subset of an approved parent stage; it cannot remove obligations. Execution-plan revisions invalidate affected receipts without automatically revoking the business approval. Platform permissions remain in force.
