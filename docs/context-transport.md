# Bounded exact review context

Opt in with `ase task create --file task.json --compact-context`, or the task
contract field `context_transport: {"version":1,"mode":"lossless"}`. Saved legacy
contracts keep their literal packet behavior; revisions preserve an existing
transport choice. This is available to every task profile and backend.

Large opted-in managed packets use readable lossless representations before packet
admission (one MiB by default, or the owner-approved bound described below). Current
source, requirements, original approved clarifications, historical lead inputs and
actual check evidence retain their identities. No SQLite migration or historical
deletion is required.

Exact duplicate bodies share a same-packet reference. Canonical JSON may share
repeated values or exact source bodies. Standard verbose unittest success lines
are grouped by class; every test name, order and line ending is retained.
Failures, skips, tracebacks and nonstandard multiline output remain literal.
Each delivered representation and reconstructed original have separate hashes;
the host verifies exact restoration. All receipt consumers expand through the
same bounded function. Unknown/cyclic/missing references, corrupt hashes and
excessive depth, node count or expanded bytes fail closed.

Opted-in sessions do not receive prior adequacy verdict prose as input to a
fresh judgment. Original requirements, scenario plans, source, logs and required
historical lead inputs remain present. The gate still checks the separate current
adequacy receipt; withholding prior prose grants no approval.

Historical deltas optionally normalize the authenticated current base from CRLF
to LF before applying edits. The original base hash is checked first and the
exact historical result hash afterward. Historical bytes are never replaced
with today's source; legacy deltas/receipts continue to use their original view.

Large requests explicitly select `handoff.context_transport` layout
`source_text_v1`. The stored request remains ordinary sealed JSON; the model gets
its complete manifest plus length-delimited literal bodies, avoiding repeated
JSON escaping. This view uses every body and is checked against the same receipt.
Old requests without the marker keep their original exact prompt formatting.
Codex's complete prompt is checked against its native character limit before
launch; raw request and packet byte limits remain independent. Other adapters
retain their existing command-line limits and fail closed when exceeded.

An owner may approve a larger packet for one lossless task with
`context_transport: {"version":1,"mode":"lossless","max_packet_bytes":N}`, where N is
an integer from 1 MiB to 2 MiB. The host derives the Runner configuration from that
approved contract field (`max_packet_bytes` N and `max_input_bytes` N + 1 MiB) when it
builds the Runner. Request content, including any handoff field, cannot raise the
bound, and the Runner rejects a configured packet limit above 2 MiB.
Each adapter keeps its own native limit: Codex still rejects a complete prompt over
1,048,576 characters, so a larger packet needs a backend that accepts it. Claude
receives the prompt on stdin after a bare `-p`, which avoids the Windows command-line
limit; older Claude receipts with the prompt as the `-p` argument still verify.

These are transport optimizations, not semantic approval or an unlimited context
window. Genuinely excessive unique content still requires a narrower justified
scope or another independently accepted bounded workflow. Missing inputs or an
unavailable reviewer cannot become PASS. Native tool-using advisory reports do
not replace the final managed independent Runner receipts and gate.
