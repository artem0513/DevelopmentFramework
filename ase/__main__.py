"""Dependency-free command interface. All output is structured JSON."""
import argparse
import json
import sqlite3
import subprocess
import sys

from .core import Engine, load, redact
from .continuity import Continuity
from .knowledge import Knowledge


def parser():
    p = argparse.ArgumentParser(description="Evidence-driven autonomous engineering")
    p.add_argument("--repo", default=".")
    p.add_argument("--home", help="State home outside source repository")
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("init", help="Inspect repository and show candidate commands")
    start = commands.add_parser("start", help="Record a confirmed JSON task contract")
    start.add_argument("contract")
    for name in ("check", "gate", "report", "approve-high-risk"):
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
    for name in ("run", "resume", "verify"):
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
        sub.add_argument("--parallel", action="store_true", help="Run disjoint read-only AC/review sessions concurrently")
        if name == "resume":
            sub.add_argument("--recover", action="store_true", help="Remove interrupted operation lock only if owner process stopped")
    status = commands.add_parser("status")
    status.add_argument("task_id", nargs="?")
    commands.add_parser("context")
    commands.add_parser("backup")
    daily = commands.add_parser("daily")
    daily.add_argument("--date")
    weekly = commands.add_parser("weekly", help="Draft seven days ending on --date; groups by stored start-date")
    weekly.add_argument("--date")
    authorize = commands.add_parser("authorize")
    authorize.add_argument("--remote", default="origin")
    authorize.add_argument("--disable", action="store_true")
    protected = commands.add_parser("authorize-protected", help="Record explicit user authorization for exact protected source paths at current revision")
    protected.add_argument("task_id")
    protected.add_argument("--path", action="append", required=True, dest="paths")
    protected.add_argument("--reason", required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("task_id")
    publish.add_argument("--execute", action="store_true")
    publish.add_argument("--remote", default="origin")
    publish.add_argument("--commit", dest="commit_message")
    decision = commands.add_parser("decision")
    decision.add_argument("--task-id")
    decision.add_argument("--context", required=True)
    decision.add_argument("--decision", required=True)
    decision.add_argument("--rationale", required=True)
    entry = commands.add_parser("time")
    entry.add_argument("task_id")
    entry.add_argument("--activity", required=True)
    entry.add_argument("--start", required=True)
    entry.add_argument("--end", required=True)
    entry.add_argument("--source", choices=["agent", "human"], default="agent")
    entry.add_argument("--confirmed", action="store_true")
    correction = commands.add_parser("correct-time", help="Correct an entry with an atomic before/after audit")
    correction.add_argument("entry_id", type=int)
    correction.add_argument("--activity")
    correction.add_argument("--start")
    correction.add_argument("--end")
    correction.add_argument("--source", choices=["agent", "human"])
    confirmation = correction.add_mutually_exclusive_group()
    confirmation.add_argument("--confirmed", dest="confirmed", action="store_true", default=None)
    confirmation.add_argument("--unconfirmed", dest="confirmed", action="store_false")
    correction.add_argument("--reason", default="Explicit CLI correction")
    session = commands.add_parser("session-summary", help="Save concise decisions and unresolved questions for resumption")
    session.add_argument("task_id")
    session.add_argument("--summary", required=True)
    session.add_argument("--decision", default="")
    session.add_argument("--unresolved", default="")
    for name in ("continuity-snapshot", "restore", "coverage"):
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
    checkpoint = commands.add_parser("checkpoint", help="Save a source-observed handoff with revision compare-and-swap")
    checkpoint.add_argument("task_id")
    checkpoint.add_argument("--expected-revision", required=True, type=int)
    checkpoint.add_argument("--file", required=True)
    source = commands.add_parser("source-record", help="Record declared source ranges and analysis gaps; no fetching")
    source.add_argument("task_id")
    source.add_argument("--file", required=True)
    source.add_argument("--expected-sequence", type=int, required=True)
    knowledge = commands.add_parser("knowledge-record", help="Append a dated, sourced historical claim")
    knowledge.add_argument("--file", required=True)
    query = commands.add_parser("knowledge-query", help="Read historical project knowledge, including superseded claims")
    query.add_argument("--topic")
    commands.add_parser("knowledge-lint", help="Check local reference structure and parallel claims without semantic authority")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    engine = None
    try:
        engine = Engine(args.repo, args.home)
        cmd = args.command
        if cmd == "init": result = engine.init()
        elif cmd == "start": result = engine.start(load(args.contract))
        elif cmd == "status": result = engine.status(args.task_id)
        elif cmd == "context": result = engine.context()
        elif cmd == "backup": result = {"backup": engine.store.backup()}
        elif cmd == "daily": result = engine.daily(args.date)
        elif cmd == "weekly": result = engine.weekly(args.date)
        elif cmd == "correct-time": result = engine.correct_time(args.entry_id, args.activity, args.start, args.end, args.source, args.confirmed, args.reason)
        elif cmd == "session-summary": result = engine.session_summary(args.task_id, args.summary, args.decision, args.unresolved)
        elif cmd == "continuity-snapshot": result = Continuity(engine.store).snapshot(args.task_id)
        elif cmd == "checkpoint": result = Continuity(engine.store).checkpoint(args.task_id, args.expected_revision, load(args.file))
        elif cmd == "restore": result = Continuity(engine.store).restore(args.task_id)
        elif cmd == "source-record": result = Continuity(engine.store).source_record(args.task_id, load(args.file), args.expected_sequence)
        elif cmd == "coverage": result = Continuity(engine.store).coverage(args.task_id)
        elif cmd == "knowledge-record": result = Knowledge(engine.store).record(load(args.file))
        elif cmd == "knowledge-query": result = Knowledge(engine.store).query(args.topic)
        elif cmd == "knowledge-lint": result = Knowledge(engine.store).lint()
        elif cmd == "decision": result = engine.decision(args.context, args.decision, args.rationale, args.task_id)
        elif cmd == "time": result = engine.record_time(args.task_id, args.activity, args.start, args.end, args.source, args.confirmed)
        elif cmd == "authorize": result = engine.authorize(args.remote, args.disable)
        elif cmd == "authorize-protected": result = engine.authorize_protected(args.task_id, args.paths, args.reason)
        elif cmd == "publish": result = engine.publish(args.task_id, args.execute, args.remote, args.commit_message)
        elif cmd == "approve-high-risk": result = engine.approve_high_risk(args.task_id)
        elif cmd == "run": result = engine.run(args.task_id, parallel=args.parallel)
        elif cmd == "resume": result = engine.run(args.task_id, resume=True, recover=args.recover, parallel=args.parallel)
        elif cmd == "verify": result = engine.verify(args.task_id, args.parallel)
        else: result = getattr(engine, cmd)(args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        status = result.get("status") or result.get("quality_gate", {}).get("status")
        # verify reports its substantive verdict in JSON; successful execution is
        # distinct from gate approval. `gate` supplies the required CI exit code.
        if cmd == "verify":
            return 3 if status == "BLOCKED" and any("session unavailable" in r for r in result.get("reasons", [])) else 0
        return 2 if status in {"REJECTED", "FAIL"} else 3 if status == "BLOCKED" else 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as ex:
        print(json.dumps({"status": "BLOCKED", "error": redact(ex)}, ensure_ascii=False))
        return 3
    finally:
        if engine:
            engine.store.close()


if __name__ == "__main__":
    sys.exit(main())
