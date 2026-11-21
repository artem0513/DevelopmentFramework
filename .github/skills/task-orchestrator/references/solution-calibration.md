# Solution calibration examples

Real tickets where correct but over-engineered changes were rejected by the team lead
("I hired a person, not an AI agent"), and the proportional solution. Use them to judge
size and mechanisms; they are not templates.

## 1. Integration lost authorization: email admins, show a banner with a reconnect link

- **Over-engineered (31 files, +545 lines):**
  - atomic "claim job" logic in the shared background-job runner, affecting every job;
  - distributed locks around connect, disconnect and email sending;
  - a custom deferred email queue with retries and stale-email cancellation, although the
    project already had a standard send-email command;
  - a per-incident ID plus a migration to deduplicate emails;
  - a token re-check inside code that already held a lock;
  - a banner that polled the server every minute.
- **Proportional (13 files, ~190 lines):** send the email through the existing command when the
  integration status changes Active -> Broken. It is a one-time transition, so no dedup is needed
  (risk category b). Check the banner state once on page load.

## 2. "Don't sync private items to the external system"

- **Over-engineered:**
  - the filter, plus a project-wide distributed lock between "edit visibility" and "sync";
  - a conditional transaction (`tx = locked ? null : BeginTransaction()`);
  - an optional constructor dependency (`ILock lock = null` plus a null guard);
  - re-reads under the lock and two extra visibility re-checks;
  - unrelated behaviour changes.
- **Proportional:** one query filter excluding private items, plus tests for it. "Visibility
  changes during a sync run" is category c, so it is listed under "Not handled".

## 3. "Allow downloads only from the project's workspace; return 403 for others"

- **Over-engineered:** the correct check, plus:
  - a 10-minute cache with hashed keys that duplicated a re-check already done on download;
  - an unrelated auth bug fix bundled into the same PR.
- **Proportional:** the workspace check returning 403, plus tests. The unrelated auth bug goes
  to a separate ticket or note.

## 4. "Duplicates in the sync log export" (a bug ticket that was a question)

- **Wrong:** went straight to a code change.
- **Right:**
  - the rows differed by version, so the expected behaviour was already in place;
  - the deliverable is a ticket comment explaining the difference, sent back to the reporter;
  - optionally, offer a small display tweak (such as column order) for the reporter to accept or decline.

## 5. "Email admins when auth lapses" (a new pipeline instead of the existing point)

- **Wrong:**
  - invented a scheduled validation job, which is a new pipeline;
  - changed the root app component to show a banner, although the lead said that component must
    not be touched;
  - a "simplified" version kept both, because the agent judged them "really needed" without asking.
- **Right:**
  - send the email at the existing point where using the token fails;
  - show the banner in the existing feature area, not the root component;
  - raise anything else as a question.

## Size rule of thumb

Sketch the minimal solution first. Either of these triggers close scrutiny of the diff:

- a diff more than about 2x that estimate;
- more than about 50 production lines for a one-filter or one-check ticket.

The finding itself names the specific unrequired hunks, unless a category (a) requirement
explains the difference.
