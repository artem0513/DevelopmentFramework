# Bounded lossless review context implementation plan

> **For agentic workers:** use superpowers:executing-plans for implementation and independent native reviewers for technical/requirements checks.

**Goal:** Deliver the complete current and authenticated historical evidence to independent Codex sessions within native input bounds, then finish the existing causal-evidence task.

**Architecture:** Reuse immutable packet files and raw-source bindings. Add an optional versioned, readable body representation with bounded exact expansion; validate both representation and original hashes. Normalize line endings only as an explicit authenticated historical delta operation. No state migration or source/history deletion.

**Tech Stack:** Python3.11+ standard library, existing SQLite/Runner/native adapters.

**Spec:** User continuation of2026-11-20 and CAUSE/CHAIN/COMPAT/LIVE/CONTEXT criteria in the current approved bugfix-evidence-20261119 contract.

## Global constraints

- Preserve legacy receipts, tasks, exact source bytes, policies, current independent-role separation and evidence limits.
- Unknown references, corrupt hashes, missing bodies, unsafe expansion and genuinely excessive unique content fail closed.
- No changes to credentials, provider configuration, CostPP3 or other projects.
- Existing generic context-redesign task remains paused; implement only the bounded transport needed here.
- Publish checked preview and durable checkpoint before the user's90-minute deadline if full acceptance cannot finish.

## Review focus

- LF/CRLF differences must not replace historical identity or obscure actual code changes.
- Repeated metadata/source bodies may share representation, but contradictory historical values remain distinct.
- Failed/skipped/exception log text and every actual successful case identity remain available.
- All receipt consumers use exact logical source bytes; packet/raw/representation hashes must not be confused.
- Genuine native limits remain enforced; unavailable or incomplete independent review never becomes PASS.

### Task1: Reproduce and implement bounded representations

Files: ase/evidence/compact.py, ase/evidence/packets.py, ase/protocol.py, ase/lead_inputs.py and exact-source receipt consumers; tests/test_context_transport.py and tests/test_lead_inputs.py.

- [ ] Add real failing exact-roundtrip, tampering/cycle/budget, mixed logs and normalized-delta tests.
- [ ] Implement a bounded readable codec and route logical-source consumers through one expansion function.
- [ ] Compact before packet-size admission, retain complete history, enforce final native prompt bound.
- [ ] Run focused existing/new regressions and verify original packet restored byte for byte.

### Task2: Actual acceptance and delivery

- [ ] Freeze source; run full unittest suite and generated assets; refresh actual Codex fixture evidence if runtime identity changes.
- [ ] Obtain fresh independent technical review; correct confirmed defects and rerun affected checks.
- [ ] Record three original-main requirement critiques and genuine independent whole-task adequacy/AC/review against current source/checks.
- [ ] Obtain actual final ase gate, or preserve its exact remaining blocker without false completion.
- [ ] Push current preview, verify remote SHA, save dated journal and continuation; verify unrelated memory preserved.

Implementation choices are agent decisions under the user's standing autonomous authorization. Raising the native model limit or silently pruning evidence is not a correction.
