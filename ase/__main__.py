"""Dependency-free command interface. All output is structured JSON."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

from .core import Engine, load, redact
from .continuity import Continuity
from .knowledge import Knowledge
from .projects import ProjectRegistry, local_config, resolve_state_home
from .project_local import validate_local_state
from .store import Store
from .tasks import TaskService
from .workflow import WorkflowController
from .gates import GateService, source_snapshot
from .journal import Journal
from .discovery import discover_capabilities, record_discovery
from .transfer import export_project, import_project
from .publishing import Publisher
from .state import read_record, text, transaction, write_record
from .credentials import CredentialError, CredentialVault, _json, _read, _safe_path


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def parser():
    p = JSONArgumentParser(description="Project-local, evidence-driven supervised workflow")
    p.add_argument("--repo", default=".")
    p.add_argument("--home", help="Explicit state home (outside source or validated project-local state)")
    p.add_argument("--project", help="Registered project UUID or workspace alias")
    commands = p.add_subparsers(dest="command", required=True)
    credentials = commands.add_parser("credentials", help="Manage encrypted local credential profiles").add_subparsers(dest="action", required=True)
    credential_import = credentials.add_parser("import")
    credential_import.add_argument("--file", required=True)
    credentials.add_parser("list")
    credential_configure = credentials.add_parser("configure")
    credential_configure.add_argument("ref")
    credential_configure.add_argument("--target", required=True)
    credential_configure.add_argument("--file", required=True)
    access = commands.add_parser("access", help="Use approved task access").add_subparsers(dest="action", required=True)
    for name in ("ensure", "request"):
        command = access.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--stage", required=True)
        command.add_argument("--credential", required=True)
        command.add_argument("--target", required=True)
        if name == "request":
            command.add_argument("--path", default="/")
            command.add_argument("--method", choices=("GET", "HEAD"), default="GET")
    commands.add_parser("init", help="Inspect repository and show candidate commands")
    capabilities = commands.add_parser("capabilities", help="Read scoped tool/skill metadata and candidate checks without executing them")
    capabilities.add_argument("--file", help="JSON with scope and backend_config; default scope is project-root build metadata")
    start = commands.add_parser("start", help="Record a confirmed JSON task contract")
    start.add_argument("contract")
    for name in ("check", "gate", "report", "approve-high-risk"):
        sub = commands.add_parser(name)
        sub.add_argument("task_id")
        if name == "approve-high-risk":
            sub.add_argument("--user-message-ref")
            sub.add_argument("--reason")
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
    protected.add_argument("--user-message-ref")
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
    project = commands.add_parser("project").add_subparsers(dest="action", required=True)
    registration = project.add_parser("register")
    registration.add_argument("--root")
    registration.add_argument("--kind", required=True)
    registration.add_argument("--timezone", help="Reporting timezone; defaults to project-local config")
    project.add_parser("list")
    attach = project.add_parser("attach")
    attach.add_argument("project_id")
    attach.add_argument("--root", required=True)
    attach.add_argument("--expected-revision", type=int, required=True)
    exporting = project.add_parser("export")
    exporting.add_argument("destination")
    exporting.add_argument("--include-verbose", action="store_true")
    importing = project.add_parser("import")
    importing.add_argument("archive")
    importing.add_argument("--root", required=True)
    importing.add_argument("--mode", choices=["copy", "restore"], default="copy")
    task = commands.add_parser("task").add_subparsers(dest="action", required=True)
    creation = task.add_parser("create")
    creation.add_argument("--file", required=True)
    for name in ("propose", "revise", "plan"):
        command = task.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--file", required=True)
        command.add_argument("--expected-revision", required=True, type=int)
    approval = task.add_parser("approve")
    approval.add_argument("task_id")
    approval_input = approval.add_mutually_exclusive_group(required=True)
    approval_input.add_argument("--file")
    approval_input.add_argument("--proposal-hash")
    approval.add_argument("--user-message-ref")
    approval.add_argument("--stage", action="append", dest="approved_stage_ids")
    discovery = task.add_parser("discover")
    discovery.add_argument("task_id")
    discovery.add_argument("--file", help="Explicit structured discovery, including source intake")
    discovery.add_argument("--expected-revision", type=int, required=True)
    for name in ("status", "check", "run", "resume", "verify", "gate"):
        command = task.add_parser(name)
        command.add_argument("task_id")
    stop = task.add_parser("stop")
    stop.add_argument("task_id")
    stop.add_argument("--reason", required=True)
    for name in ("authorize-protected", "approve-high-risk"):
        command = task.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--user-message-ref", required=True)
        command.add_argument("--reason", required=True)
        if name == "authorize-protected":
            command.add_argument("--path", action="append", required=True, dest="paths")
    session = commands.add_parser("session").add_subparsers(dest="action", required=True)
    for name in ("start", "finish"):
        command = session.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("session_id", nargs="?")
        command.add_argument("--session-id", dest="session_option")
        if name == "finish":
            command.add_argument("--file", required=True)
            command.add_argument("--closure-key", required=True)
            command.add_argument("--expected-revision", required=True, type=int)
            command.add_argument("--expected-context-revision", required=True, type=int)
    for name in ("worklog", "meeting"):
        command = commands.add_parser(name, aliases=["meeting-report"] if name == "meeting" else [])
        command.add_argument("--start", "--from", dest="start", required=True)
        command.add_argument("--until", required=True)
        command.add_argument("--timezone")
        if name == "meeting":
            command.add_argument("--as-of-end", action="store_true")
    knowledge = commands.add_parser("knowledge").add_subparsers(dest="action", required=True)
    retrieval = knowledge.add_parser("retrieve")
    retrieval.add_argument("--file", required=True, help="Object containing explicit scope and optional topic/refresh")
    observations = knowledge.add_parser("observations")
    observations.add_argument("--file", required=True)
    observations.add_argument("--expected-revision", type=int, required=True)
    return p


def _task_command(store, action, args):
    tasks = TaskService(store)
    if action == "create":
        return tasks.create(load(args.file))
    task = tasks.snapshot(args.task_id)
    if action == "status":
        return {**task, "readiness": tasks.readiness(args.task_id)}
    if action == "revise":
        return tasks.revise(args.task_id, load(args.file), expected_revision=args.expected_revision)
    if task["legacy_v1"]:
        raise ValueError("New execution and approvals require a reviewed v2 task contract; legacy history remains readable")
    if action == "propose":
        return tasks.propose(args.task_id, load(args.file), expected_revision=args.expected_revision)
    if action == "plan":
        return tasks.update_execution_plan(args.task_id, load(args.file), expected_revision=args.expected_revision)
    if action == "approve":
        if args.file:
            if args.user_message_ref is not None or args.approved_stage_ids is not None:
                raise ValueError("Approval file cannot be mixed with approval flags")
            payload = load(args.file)
        else:
            payload = {"proposal_hash": args.proposal_hash, "user_message_ref": args.user_message_ref, "approved_stage_ids": args.approved_stage_ids}
        return tasks.approve(args.task_id, **payload)
    if action == "discover":
        payload = load(args.file) if args.file else discover_capabilities(store.project,
            scope={"resources": task["contract"]["scope"]["resources"]}, backend_config={})
        return record_discovery(store, args.task_id, payload, expected_revision=args.expected_revision)
    if action == "gate":
        return GateService(store).gate(args.task_id)
    if action in {"authorize-protected", "approve-high-risk"}:
        text(args.user_message_ref, "user_message_ref", limit=2000)
        text(args.reason, "reason", limit=4000)
        source = source_snapshot(store, task["contract"], task_id=args.task_id)
        payload = {"fingerprint": source["fingerprint"], "contract_hash": task["contract_hash"],
                   "reason": args.reason, "user_message_ref": args.user_message_ref}
        kind = "high_risk_approval"
        if action == "authorize-protected":
            from .workflow import _target
            payload["paths"] = list(dict.fromkeys(_target(store.repo.resolve(), value).relative_to(store.repo).as_posix() for value in args.paths))
            kind = "protected_approval"
        with transaction(store.db) as db:
            old = read_record(db, kind, args.task_id)
            write_record(db, kind, args.task_id, payload, expected_revision=old["revision"])
        return payload
    controller = WorkflowController(store)
    if action == "check":
        return controller.check(args.task_id)
    if action == "stop":
        return controller.request_stop(args.task_id, reason=args.reason)
    if action in {"run", "resume", "verify"}:
        if task["state"] == "COMPLETED":
            if action == "verify":
                return controller.verify(args.task_id)
            return GateService(store).gate(args.task_id)
        # Prevent even backend probing until at least one current stage is approved.
        approved = task.get("approved_stage_ids", []) or ([task["contract"]["stages"][0]["id"]] if task["contract"].get("parent_task_id") else [])
        if not approved:
            raise ValueError("Current stage requires explicit approval of a reviewed v2 proposal")
        for stage_id in approved:
            tasks.require_approval(args.task_id, stage_id)
        if action == "verify":
            return controller.verify(args.task_id)
        return controller.run(args.task_id, resume=action == "resume")
    raise ValueError("Unknown task command")


def _state_project(args):
    """A UUID selects within the repo/cwd registry, never a filesystem root."""
    if args.project:
        if Path(args.project).expanduser().is_dir():
            return args.project
        if local_config(args.repo) is None:
            return args.project
    return args.repo


def _project_command(args):
    selection = getattr(args, "root", None) or _state_project(args)
    home = resolve_state_home(args.home, project=selection)
    if args.action == "import":
        return asdict(import_project(Path(args.archive), home, root=Path(args.root), mode=args.mode))
    with_registry = ProjectRegistry(home, project=selection)
    try:
        if args.action == "register":
            config = local_config(selection)
            if args.timezone is None and config is None:
                raise ValueError("--timezone is required without project-local configuration")
            root = config["root"] if config else args.root or args.repo
            return asdict(with_registry.register(root, kind=args.kind, timezone=args.timezone))
        if args.action == "attach":
            return asdict(with_registry.attach(args.project_id, args.root, expected_revision=args.expected_revision))
        return {"projects": [dict(row) for row in with_registry.db.execute(
            "SELECT p.project_id,p.storage_key,p.kind,p.timezone,p.status,p.revision,w.workspace_id,w.root "
            "FROM projects p JOIN workspaces w ON p.current_workspace_id=w.workspace_id ORDER BY p.created,p.project_id")]}
    finally:
        with_registry.close()


def _credential_command(args):
    selection = _state_project(args)
    config = local_config(selection)
    home = resolve_state_home(args.home, project=selection)
    repo_path = selection if Path(selection).expanduser().is_dir() else args.repo
    repo = config["root"] if config else Path(repo_path).expanduser().resolve()
    if args.project and not Path(args.project).expanduser().is_dir():
        registry = ProjectRegistry(home, project=selection)
        try:
            repo = registry.resolve(args.project).root
        finally:
            registry.close()
    # Do not erase a supplied external alias before the credential path check.
    supplied = args.home if args.home is not None else (os.environ.get("ASE_HOME") if config is None else None)
    if supplied is not None:
        _safe_path(Path(supplied).expanduser())
    valid_local = validate_local_state(home, project_root=repo)
    if home.is_relative_to(repo) and not valid_local:
        raise CredentialError("unsafe_path")
    vault = CredentialVault(home)
    if args.action == "import":
        source = _safe_path(args.file)
        if source.is_relative_to(repo):
            raise CredentialError("unsafe_path")
        return {"credentials": vault.import_file(args.file)}
    if args.action == "list":
        return {"credentials": vault.list()}
    config = _json(_read(_safe_path(args.file)))
    return vault.configure(args.ref, args.target, config)


def main(argv=None):
    engine = store = None
    args = None
    try:
        args = parser().parse_args(argv)
        cmd = args.command
        if cmd == "credentials":
            result = _credential_command(args)
        elif cmd == "project" and args.action != "export":
            result = _project_command(args)
        else:
            if args.project and local_config(args.project) is not None:
                store = Store(args.project, args.home)
            elif args.project:
                registry = ProjectRegistry(args.home, project=_state_project(args))
                try:
                    selected = registry.resolve(args.project)
                finally:
                    registry.close()
                store = Store(selected)
            else:
                store = Store(args.repo, args.home)
            if cmd == "task":
                result = _task_command(store, args.action, args)
            elif cmd == "access":
                controller = WorkflowController(store)
                access = {"credential_ref": args.credential, "target": args.target}
                if args.action == "ensure":
                    result = controller.ensure_access(args.task_id, args.stage, access)
                else:
                    result = controller.request_access(args.task_id, args.stage, access, path=args.path, method=args.method)
            elif cmd == "init":
                result = {"project_id": store.project_id, "workspace_id": store.workspace_id, "root": str(store.repo),
                          "discovery": discover_capabilities(store.project, scope={"resources": ["."]}, backend_config={})}
            elif cmd == "capabilities":
                payload = load(args.file) if args.file else {"scope": {"resources": ["."]}, "backend_config": {}}
                result = discover_capabilities(store.project, **payload)
            elif cmd == "project":
                result = export_project(store.project, Path(args.destination), include_verbose=args.include_verbose)
            elif cmd in {"run", "resume", "verify"}:
                result = _task_command(store, cmd, args)
            elif cmd in {"authorize-protected", "approve-high-risk"}:
                result = _task_command(store, cmd, args)
            elif cmd == "publish":
                result = Publisher(store).publish(args.task_id, args.execute, args.remote, args.commit_message)
            elif cmd == "authorize":
                result = Publisher(store).authorize(args.remote, args.disable)
            elif cmd in {"check", "gate"}:
                result = _task_command(store, cmd, args)
            elif cmd == "status":
                result = ({**TaskService(store).snapshot(args.task_id), "readiness": TaskService(store).readiness(args.task_id)}
                          if args.task_id else {"project_id": store.project_id, "workspace_id": store.workspace_id,
                          "root": str(store.repo), "tasks": [dict(row) for row in store.db.execute("SELECT id,status,updated FROM tasks ORDER BY updated DESC")]})
            elif cmd == "context":
                result = {"project_id": store.project_id, "repository": str(store.repo), "journal": Journal(store).context(),
                          "tasks": [dict(row) for row in store.db.execute("SELECT id,request,status,updated FROM tasks ORDER BY updated DESC LIMIT 20")],
                          "decisions": [dict(row) for row in store.db.execute("SELECT * FROM decisions ORDER BY id DESC LIMIT 20")],
                          "sessions": [dict(row) for row in store.db.execute("SELECT * FROM sessions ORDER BY id DESC LIMIT 10")]}
            elif cmd == "backup": result = {"backup": store.backup()}
            elif cmd == "continuity-snapshot": result = Continuity(store).snapshot(args.task_id)
            elif cmd == "checkpoint": result = Continuity(store).checkpoint(args.task_id, args.expected_revision, load(args.file))
            elif cmd == "restore": result = Continuity(store).restore(args.task_id)
            elif cmd == "source-record": result = Continuity(store).source_record(args.task_id, load(args.file), args.expected_sequence)
            elif cmd == "coverage": result = Continuity(store).coverage(args.task_id)
            elif cmd == "knowledge-record": result = Knowledge(store).record(load(args.file))
            elif cmd == "knowledge-query": result = Knowledge(store).query(args.topic)
            elif cmd == "knowledge-lint": result = Knowledge(store).lint()
            elif cmd == "knowledge":
                payload = load(args.file)
                if args.action == "retrieve":
                    result = Knowledge(store).retrieve(**payload)
                else:
                    result = Knowledge(store).record_observations(**payload, expected_revision=args.expected_revision)
            elif cmd == "session":
                if args.session_id and args.session_option:
                    raise ValueError("Supply the session identifier once")
                args.session_id = args.session_id or args.session_option
                if not args.session_id:
                    raise ValueError("Session identifier is required")
                journal = Journal(store)
                result = (journal.start_session(args.task_id, args.session_id) if args.action == "start" else
                          journal.finish_session(args.task_id, args.session_id, load(args.file), closure_key=args.closure_key,
                              expected_revision=args.expected_revision, expected_context_revision=args.expected_context_revision))
            elif cmd in {"worklog", "meeting", "meeting-report"}:
                journal = Journal(store)
                result = (journal.worklog(args.start, args.until, timezone=args.timezone or store.project.timezone) if cmd == "worklog" else
                          journal.meeting_report(args.start, args.until, timezone=args.timezone or store.project.timezone, as_of_end=args.as_of_end))
            elif cmd == "start" and load(args.contract).get("schema_version") == 2:
                result = TaskService(store).create(load(args.contract))
            elif cmd == "report" and not TaskService(store).snapshot(args.task_id)["legacy_v1"]:
                result = {"task": TaskService(store).snapshot(args.task_id), "quality_gate": GateService(store).gate(args.task_id),
                          "project_id": store.project_id, "workspace_id": store.workspace_id}
            else:
                # Existing Git-only tools remain available for legacy history,
                # time and continuity. Public execution never reaches Engine.run.
                engine = Engine(store.repo, store.state_home)
                if cmd == "init": result = engine.init()
                elif cmd == "start": result = engine.start(load(args.contract))
                elif cmd == "daily": result = engine.daily(args.date)
                elif cmd == "weekly": result = engine.weekly(args.date)
                elif cmd == "correct-time": result = engine.correct_time(args.entry_id, args.activity, args.start, args.end, args.source, args.confirmed, args.reason)
                elif cmd == "session-summary": result = engine.session_summary(args.task_id, args.summary, args.decision, args.unresolved)
                elif cmd == "decision": result = engine.decision(args.context, args.decision, args.rationale, args.task_id)
                elif cmd == "time": result = engine.record_time(args.task_id, args.activity, args.start, args.end, args.source, args.confirmed)
                elif cmd == "authorize": result = engine.authorize(args.remote, args.disable)
                elif cmd == "authorize-protected": result = engine.authorize_protected(args.task_id, args.paths, args.reason)
                elif cmd == "publish": result = engine.publish(args.task_id, args.execute, args.remote, args.commit_message)
                elif cmd == "approve-high-risk": result = engine.approve_high_risk(args.task_id)
                else: result = getattr(engine, cmd)(args.task_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        status = result.get("status") or result.get("quality_gate", {}).get("status")
        # verify reports its substantive verdict in JSON; successful execution is
        # distinct from gate approval. `gate` supplies the required CI exit code.
        if cmd == "verify" and status != "BLOCKED":
            return 3 if status == "BLOCKED" and any("session unavailable" in r for r in result.get("reasons", [])) else 0
        return 2 if status in {"REJECTED", "FAIL"} else 3 if status == "BLOCKED" else 0
    except (ValueError, OSError, KeyError, TypeError, sqlite3.Error, subprocess.SubprocessError) as ex:
        raw_args = sys.argv[1:] if argv is None else argv
        sensitive_command = (args is not None and args.command in {"credentials", "access"}) or (args is None and any(x in {"credentials", "access"} for x in raw_args))
        error = (ex.code if isinstance(ex, CredentialError) else "command_failed") if sensitive_command else redact(ex)
        print(json.dumps({"status": "BLOCKED", "error": error}, ensure_ascii=False))
        return 3
    finally:
        if engine:
            engine.store.close()
        if store:
            store.close()


if __name__ == "__main__":
    sys.exit(main())
