# Historical project knowledge

Use for durable decisions, recurring problems and user corrections that will matter after the current task. Before relying on previous conclusions, run `knowledge-query --topic TOPIC` in the canonical project. Retrieve relevant topics rather than loading every historical claim. Current user instructions, source evidence and repository state retain authority.

Use `knowledge-record --file PATH` with templates/knowledge.json. Each claim carries a topic, statement, timezone-aware observation date and nonempty source_refs. When describing code, include its repository locator and full repository_revision when available; if unknown, state the uncertainty in the claim and do not present the claim as current code truth. Cite evidence instead of copying original chats, logs or transcripts. Do not store credentials.

Knowledge is dated history, not a promise that code still behaves that way. Reverify drift-prone facts against current sources before acting. New evidence replaces a claim by recording a new dated claim with supersedes equal to the prior claim id. The old record remains available and visibly superseded. A stale or mismatched supersession must fail; query the latest records, reconcile and retry. Never erase an inconvenient decision or treat a past approval as new authority.

Run `knowledge-lint` when reviewing accumulated knowledge or before a substantial handoff. Multiple unsuperseded claims for one topic require review; that signal is not proof of a semantic contradiction. Resolve conflicting evidence in the actual task and append the justified history. No recurring automation is created by this instruction.

SQLite is canonical. Markdown exports and indexes may be disposable views only. Task progress stays in the revision-checked checkpoint; knowledge records explain durable decisions without becoming a second task tracker or an alternative quality gate. No blanket subagent delegation or environment changes are implied.
