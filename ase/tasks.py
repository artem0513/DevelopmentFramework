"""Versioned task intent, explicit approval, and bounded execution plans."""
import copy
import json
import uuid

from .state import append_event, encode, hash_value, identifier, read_record, text, transaction, write_record
from .store import now
from .profiles import stage_profile, validate_access, validate_evidence_spec, validate_mixed_mapping, stage_evidence_specs, has_software
from .projects import _safe_state


STATES = {"DRAFT", "RESEARCHING", "AWAITING_APPROVAL", "READY", "EXECUTING", "VALIDATING",
          "REVIEWING", "DIAGNOSING", "BLOCKED", "PAUSED", "COMPLETED", "CANCELLED"}
PROFILES = {"software", "infrastructure", "document", "research", "mixed"}
DEFAULT_POLICY = {"recovery_mode": "progress", "stagnation_limit": 3,
                  "max_stage_cycles": None, "max_task_cycles": None, "repeat_failure_limit": 2,
                  "max_diagnostic_passes": None, "diagnostic_timeout_seconds": 1200,
                  "active_time_budget_seconds": None, "backend": "codex"}


def _scope(value):
    if not isinstance(value, dict) or set(value) - {"resources", "effects"}:
        raise ValueError("scope requires resources and effects")
    result = {}
    for key in ("resources", "effects"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 1000:
            raise ValueError("Invalid scope " + key)
        result[key] = list(dict.fromkeys(text(item, key, limit=2000) for item in items))
    return result


def _within(inner, outer):
    return all(set(inner[k]).issubset(outer[k]) for k in ("resources", "effects"))


def _unique(items, name):
    if not isinstance(items, list) or len(items) > 1000:
        raise ValueError(name + " must be a bounded list")
    ids = [identifier(item.get("id"), name + " id") for item in items if isinstance(item, dict)]
    if len(ids) != len(items) or len(ids) != len(set(ids)):
        raise ValueError(name + " requires unique object IDs")
    return set(ids)


def validate_contract_v2(value):
    allowed = {"id", "schema_version", "profile", "request", "scope", "acceptance", "evidence", "commands",
               "stages", "assumptions", "unknowns", "sources", "capabilities", "execution_policy", "reporting",
               "risk", "protected_paths", "parent_task_id", "parent_stage_id", "test_adequacy", "lead_review"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Unknown task field; approval must be recorded separately")
    c = copy.deepcopy(value)
    if c.get("schema_version") != 2 or c.get("profile") not in PROFILES:
        raise ValueError("Task requires schema_version 2 and a supported profile")
    text(c.get("request"), "request")
    c["scope"] = _scope(c.get("scope"))
    if 'lead_review' in c:
        from .lead_review import validate_policy
        validate_policy(c['lead_review'])
    c.setdefault("risk", "MEDIUM")
    if c["risk"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("Invalid risk")
    for key in ("sources", "assumptions", "unknowns"):
        c.setdefault(key, [])
        if not isinstance(c[key], list):
            raise ValueError(key + " must be a list")
    unknown_ids = _unique(c["unknowns"], "unknowns")
    ac_ids = _unique(c.get("acceptance"), "acceptance")
    if not ac_ids:
        raise ValueError("Task requires observable acceptance criteria")
    checks = c.get("evidence", c.get("commands", []))
    check_ids = _unique(checks, "evidence")
    if not checks:
        raise ValueError("Task requires evidence specifications")
    c["evidence"] = checks
    for spec in checks:
        validate_evidence_spec(spec, c["profile"])
    for ac in c["acceptance"]:
        text(ac.get("expected"), "expected")
        if type(ac.get("mandatory", True)) is not bool:
            raise ValueError("mandatory must be boolean")
        refs = ac.get("evidence_ids", ac.get("commands", []))
        if not isinstance(refs, list) or not set(refs).issubset(check_ids) or (ac.get("mandatory", True) and not refs):
            raise ValueError("Mandatory AC requires known evidence IDs")
        ac["evidence_ids"] = refs
    stages = c.get("stages")
    stage_ids = _unique(stages, "stages")
    if not stage_ids:
        raise ValueError("Task requires explicit stages")
    for stage in stages:
        if c["profile"] != "mixed" and "profile" in stage and stage["profile"] != c["profile"]:
            raise ValueError("Different domain stages require a mixed parent profile")
        if "required_child_task_id" in stage:
            identifier(stage["required_child_task_id"], "required child task id")
            if stage["required_child_task_id"] == c.get("id"):
                raise ValueError("A task cannot require itself as a child")
        stage["scope"] = _scope(stage.get("scope", c["scope"]))
        if not _within(stage["scope"], c["scope"]):
            raise ValueError("Stage scope exceeds task scope")
        for key, known in (("acceptance_ids", ac_ids), ("depends_on", stage_ids), ("unknown_ids", unknown_ids)):
            stage.setdefault(key, [])
            if not isinstance(stage[key], list) or not set(stage[key]).issubset(known):
                raise ValueError("Stage references unknown " + key)
        actions = stage.get("actions", [])
        _unique(actions, "actions")
        if actions and "command" not in stage["scope"]["effects"]:
            raise ValueError("Stage actions require an approved command effect")
        for action in actions:
            if set(action) - {"id", "argv", "observation_argv", "timeout", "access"}:
                raise ValueError("Unknown action fields")
            if "access" in action:
                validate_access(action["access"])
                if action["access"]["target"] not in stage["scope"]["resources"]:
                    raise ValueError("Access target exceeds approved stage resources")
            for key in ("argv", "observation_argv"):
                validate_evidence_spec({"id": action["id"], "kind": "command", "argv": action.get(key), "timeout": action.get("timeout", 300)}, c["profile"])
    for spec in checks:
        if "access" in spec and not any(spec["access"]["target"] in stage["scope"]["resources"]
                and spec["id"] in {item["id"] for item in stage_evidence_specs(c, checks, stage["id"])} for stage in stages):
            raise ValueError("Access target exceeds approved stage resources")
    required_children = [s["required_child_task_id"] for s in stages if "required_child_task_id" in s]
    if len(required_children) != len(set(required_children)):
        raise ValueError("A required child must map to exactly one parent stage")
    validate_mixed_mapping(c)
    if 'test_adequacy' in c:
        quality = c['test_adequacy']
        if (not isinstance(quality, dict) or set(quality) != {'version', 'required'}
                or type(quality['version']) is not int or quality['version'] != 1
                or quality['required'] is not True or not has_software(c)):
            raise ValueError('test_adequacy requires version 1, required true and software scope')
    visiting, visited = set(), set()
    by_id = {s["id"]: s for s in stages}

    def visit(stage_id):
        if stage_id in visiting:
            raise ValueError("Stage dependency cycle")
        if stage_id not in visited:
            visiting.add(stage_id)
            for dependency in by_id[stage_id]["depends_on"]:
                visit(dependency)
            visiting.remove(stage_id)
            visited.add(stage_id)
    for stage_id in by_id:
        visit(stage_id)
    for unknown in c["unknowns"]:
        text(unknown.get("statement"), "unknown statement")
        if unknown.get("status", "UNRESOLVED") not in {"UNRESOLVED", "CONFIRMED", "RESOLVED"}:
            raise ValueError("Invalid unknown status")
        if not set(unknown.get("blocking_stage_ids", [])).issubset(stage_ids):
            raise ValueError("Unknown references missing stage")
    policy = dict(DEFAULT_POLICY)
    supplied = c.get("execution_policy", {})
    if not isinstance(supplied, dict) or set(supplied) - set(DEFAULT_POLICY):
        raise ValueError("Unknown execution_policy field")
    policy.update(supplied)
    if "recovery_mode" not in supplied and any(supplied.get(key) is not None
            for key in ("max_stage_cycles", "max_task_cycles", "max_diagnostic_passes")):
        policy["recovery_mode"] = "fixed"
    if policy["recovery_mode"] not in {"fixed", "progress"}:
        raise ValueError("Invalid recovery mode")
    if policy["recovery_mode"] == "fixed":
        for key, default in (("max_stage_cycles", 3), ("max_task_cycles", 12), ("max_diagnostic_passes", 1)):
            if key not in supplied:
                policy[key] = default
    for key in ("max_stage_cycles", "max_task_cycles", "repeat_failure_limit", "max_diagnostic_passes", "diagnostic_timeout_seconds", "stagnation_limit"):
        if key in {"max_stage_cycles", "max_task_cycles", "max_diagnostic_passes"} and policy[key] is None and policy["recovery_mode"] == "progress":
            continue
        if type(policy[key]) is not int or not 1 <= policy[key] <= (3600 if key.endswith("seconds") else 100):
            raise ValueError("Invalid execution policy " + key)
    budget = policy["active_time_budget_seconds"]
    if budget is not None and (type(budget) is not int or budget <= 0):
        raise ValueError("Invalid active time budget")
    if policy["backend"] not in {"codex", "claude", "copilot"}:
        raise ValueError("Unknown backend")
    c["execution_policy"] = policy
    c.setdefault("reporting", {"timezone": "local"})
    text(encode(c), "contract", limit=200000)
    return c


def validate_child_obligations(child_contract, parent_contract, stage, child_id):
    """A child inherits one concrete domain and cannot replace required business ACs."""
    if child_contract["profile"] != stage_profile(parent_contract, stage["id"]):
        raise ValueError("Child profile must match its parent stage domain")
    if stage.get("required_child_task_id") != child_id:
        return
    child_acceptance = {ac["id"]: ac for ac in child_contract["acceptance"]}
    for ac in parent_contract["acceptance"]:
        if ac["id"] not in stage["acceptance_ids"] or not ac.get("mandatory", True):
            continue
        mapped = child_acceptance.get(ac["id"])
        if not mapped or mapped["expected"] != ac["expected"] or not mapped.get("mandatory", True):
            raise ValueError("Required child acceptance must retain its parent business obligation: " + ac["id"])


class TaskService:
    def __init__(self, store):
        self.store = store

    def snapshot(self, task_id):
        task = self.store.task(identifier(task_id, "task id"))
        record = read_record(self.store.db, "task", task_id)
        if record["payload"] is None:
            return {"task_id": task_id, "revision": 0, "state": task["status"], "contract": task["contract"],
                    "legacy_v1": True, "contract_hash": hash_value(task["contract"]), "proposal_hash": None}
        return {**record["payload"], "revision": record["revision"], "legacy_v1": False}

    def create(self, contract):
        c = validate_contract_v2(contract)
        task_id = identifier(c.pop("id", None) or uuid.uuid4().hex[:16], "task id")
        parent = None
        if c.get("parent_task_id"):
            parent = self.require_approval(c["parent_task_id"], c.get("parent_stage_id"))
            parent_stage = next(s for s in parent["contract"]["stages"] if s["id"] == c["parent_stage_id"])
            if not _within(c["scope"], parent_stage["scope"]):
                raise ValueError("Child scope exceeds approved parent stage")
            if parent['contract'].get('test_adequacy') and has_software(c):
                c['test_adequacy'] = copy.deepcopy(parent['contract']['test_adequacy'])
            if parent['contract'].get('lead_review'):
                c['lead_review'] = copy.deepcopy(parent['contract']['lead_review'])
            validate_child_obligations(c, parent["contract"], parent_stage, task_id)
        payload = {"task_id": task_id, "state": "DRAFT", "contract": c, "contract_hash": hash_value(c),
                   "proposal_hash": None, "approved_stage_ids": [], "created": now()}
        if parent:
            payload.update(state="READY", parent_proposal_hash=parent["proposal_hash"],
                           proposal_hash=hash_value({"parent_proposal_hash": parent["proposal_hash"], "contract_hash": hash_value(c)}),
                           approved_stage_ids=[s["id"] for s in c["stages"]], inherited_approval=True)
        with transaction(self.store.db) as db:
            db.execute("INSERT INTO tasks VALUES(?,?,?,?,?,?)", (task_id, c["request"], "DRAFT", encode(c), now(), now()))
            self._save(payload, 0)
            write_record(db, "execution_plan", task_id, {"checks": c["evidence"], "plan_hash": hash_value(c["evidence"])}, expected_revision=0)
            write_record(db, "task_version", task_id + ":1", payload, expected_revision=0)
        return self.snapshot(task_id)

    def _save(self, payload, expected_revision):
        db = self.store.db
        write_record(db, "task", payload["task_id"], payload, expected_revision=expected_revision)
        db.execute("UPDATE tasks SET status=?,updated=? WHERE id=?", (payload["state"], now(), payload["task_id"]))

    def revise(self, task_id, contract, *, expected_revision):
        """A changed goal creates a new version and requires a fresh proposal."""
        if not isinstance(contract, dict) or not isinstance(contract.get("execution_policy", {}), dict):
            raise ValueError("Task revision and execution policy must be objects")
        incoming = copy.deepcopy(contract)
        current = self.snapshot(task_id)
        if not current["legacy_v1"]:
            policy = copy.deepcopy(current["contract"]["execution_policy"])
            policy.update(incoming.get("execution_policy", {}))
            if "recovery_mode" not in policy:
                policy["recovery_mode"] = "fixed"
            incoming["execution_policy"] = policy
            if current['contract'].get('test_adequacy'):
                incoming.setdefault('test_adequacy', copy.deepcopy(current['contract']['test_adequacy']))
            if current['contract'].get('lead_review'):
                incoming.setdefault('lead_review', copy.deepcopy(current['contract']['lead_review']))
        c = validate_contract_v2(incoming)
        supplied_id = c.pop("id", task_id)
        if supplied_id != task_id:
            raise ValueError("Revision cannot change task identity")
        with transaction(self.store.db) as db:
            task = self.snapshot(task_id)
            if task['contract'].get('lead_review') or c.get('lead_review'):
                from .lead_review import archive_approved_intent
                archive_approved_intent(self.store, task)
            if task["state"] in {"COMPLETED", "CANCELLED"}:
                raise ValueError("Terminal task needs a new task")
            legacy_budget = task.get("legacy_budget")
            if task["legacy_v1"]:
                maximum = task["contract"].get("max_attempts", 2)
                if type(maximum) is not int or not 1 <= maximum <= 5:
                    raise ValueError("Legacy attempt ceiling is invalid; inspect the original task")
                progress_path = self.store.root / "tasks" / task_id / "progress.json"
                _safe_state(progress_path)
                audited = db.execute("SELECT COUNT(*) FROM transitions WHERE task_id=? AND status='IMPLEMENTING'", (task_id,)).fetchone()[0]
                attempts = audited
                if progress_path.exists():
                    if not progress_path.is_file() or progress_path.stat().st_size > 32000:
                        raise ValueError("Invalid legacy attempt history")
                    try:
                        progress = json.loads(progress_path.read_text(encoding="utf-8-sig"))
                    except (ValueError, UnicodeError):
                        raise ValueError("Malformed legacy attempt history") from None
                    spent = progress.get("attempts") if isinstance(progress, dict) else None
                    if type(spent) is not int or not 0 <= spent <= maximum:
                        raise ValueError("Invalid legacy attempt history; refusing to reset the budget")
                    attempts = max(audited, spent)
                elif task["state"] in {"IMPLEMENTING", "ESCALATED", "RECOVERED"} or (progress_path.parent.exists() and any(progress_path.parent.glob("implementation-*"))):
                    raise ValueError("Legacy attempt history is missing; restore it before continuation")
                if attempts > maximum:
                    raise ValueError("Legacy attempt history exceeds its ceiling")
                legacy_budget = {"max_attempts": maximum, "attempts_at_migration": attempts}
                write_record(db, "task_version", task_id + ":legacy", task, expected_revision=0)
                current_budget = read_record(db, "budget", task_id)
                budget = current_budget["payload"] or {"task_cycles": 0, "stages": {}, "diagnostics": {}, "failures": {}}
                budget["task_cycles"] = max(budget["task_cycles"], attempts)
                key = task_id + ":" + c["stages"][0]["id"]
                budget["stages"][key] = max(budget["stages"].get(key, 0), attempts)
                write_record(db, "budget", task_id, budget, expected_revision=current_budget["revision"])
            if legacy_budget:
                for key in ("max_stage_cycles", "max_task_cycles"):
                    ceiling = c["execution_policy"][key]
                    c["execution_policy"][key] = min(ceiling, legacy_budget["max_attempts"]) if ceiling is not None else legacy_budget["max_attempts"]
                c["execution_policy"]["recovery_mode"] = "fixed"
                if c["execution_policy"]["max_diagnostic_passes"] is None:
                    c["execution_policy"]["max_diagnostic_passes"] = 1
            if c.get("parent_task_id") != task["contract"].get("parent_task_id") or c.get("parent_stage_id") != task["contract"].get("parent_stage_id"):
                raise ValueError("Revision cannot change parent authority")
            if c.get("parent_task_id"):
                parent = self.require_approval(c["parent_task_id"], c["parent_stage_id"])
                stage = next(s for s in parent["contract"]["stages"] if s["id"] == c["parent_stage_id"])
                if not _within(c["scope"], stage["scope"]):
                    raise ValueError("Child scope exceeds approved parent")
                validate_child_obligations(c, parent["contract"], stage, task_id)
            payload = {k: v for k, v in task.items() if k not in {"revision", "legacy_v1", "proposal"}}
            payload.update(contract=c, contract_hash=hash_value(c), proposal_hash=None, approved_stage_ids=[], state="DRAFT")
            if legacy_budget:
                payload["legacy_budget"] = legacy_budget
            if c.get("parent_task_id"):
                payload["parent_proposal_hash"] = parent["proposal_hash"]
            self._save(payload, expected_revision)
            db.execute("UPDATE tasks SET contract=?,request=? WHERE id=?", (encode(c), c["request"], task_id))
            old = read_record(db, "execution_plan", task_id)
            write_record(db, "execution_plan", task_id, {"checks": c["evidence"], "plan_hash": hash_value(c["evidence"])}, expected_revision=old["revision"])
            write_record(db, "task_version", task_id + ":" + str(expected_revision + 1), payload, expected_revision=0)
        return self.snapshot(task_id)

    def readiness(self, task_id, stage_id=None):
        from .source_coverage import source_availability, availability_gaps, read_persisted_intake
        task = self.snapshot(task_id)
        if task["legacy_v1"]:
            if stage_id is not None:
                raise ValueError("legacy_v1 has no v2 readiness stage; revise the task contract first")
            present = self.store.db.execute("SELECT 1 FROM task_discoveries WHERE task_id=? LIMIT 1", (task_id,)).fetchone()
            return {"legacy_v1": True, "discovery_present": bool(present), "required_source_gaps": [],
                    "source_availability": [], "intake_validation": {"status": "LEGACY_UNASSESSED", "source_statuses": []},
                    "untracked_gap_ids": [], "clarity": "UNASSESSED", "semantic_validation": False, "execution_authorized": False,
                    "limitations": ["Legacy history requires a reviewed v2 task contract before new execution"]}
        row = self.store.db.execute("SELECT payload FROM task_discoveries WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        discovery = json.loads(row[0]) if row else None
        intake_state = read_persisted_intake(discovery)
        intake = intake_state["intake"]
        gaps = [g for g in [*intake.get("gaps", []), *intake.get("contradictions", [])] if g.get("required", True) and g.get("status", "UNRESOLVED").upper() not in {"RESOLVED", "CONFIRMED"}]
        availability = source_availability(self.store, task["contract"], discovery, stage_id)
        gaps.extend(availability_gaps(availability))
        known = {u["id"] for u in task["contract"].get("unknowns", [])}
        if stage_id is not None:
            stages = task["contract"]["stages"]
            if stage_id not in {stage["id"] for stage in stages}:
                raise ValueError("Unknown readiness stage")
            relevant = []
            for gap in gaps:
                affected = gap.get("blocking_stage_ids")
                if affected is None:
                    unknown = next((u for u in task["contract"].get("unknowns", []) if u["id"] == gap["id"]), None)
                    if unknown and unknown.get("status", "UNRESOLVED") == "UNRESOLVED":
                        affected = set(unknown.get("blocking_stage_ids", [])) | {
                            stage["id"] for stage in stages if gap["id"] in stage.get("unknown_ids", [])}
                if not affected or not set(affected).issubset({stage["id"] for stage in stages}):
                    relevant.append(gap)
                    continue
                affected = set(affected)
                while True:
                    expanded = affected | {stage["id"] for stage in stages if set(stage.get("depends_on", [])) & affected}
                    if expanded == affected:
                        break
                    affected = expanded
                if stage_id in affected:
                    relevant.append(gap)
            gaps = relevant
        gaps.extend(intake_state["compatibility_gaps"])
        return {"discovery_present": bool(row), "required_source_gaps": gaps, "source_availability": availability, "intake_validation": intake_state["validation"],
                "untracked_gap_ids": [g["id"] for g in gaps if g["id"] not in known],
                "clarity": "BLOCKED" if gaps else "SCOPED" if row else "UNASSESSED",
                "semantic_validation": False}

    def propose(self, task_id, proposal, *, expected_revision):
        if not isinstance(proposal, dict) or set(proposal) - {"summary", "stage_ids", "business", "technical", "steps", "verification", "limitations", "assumptions"}:
            raise ValueError("Invalid proposal fields")
        text(proposal.get("summary"), "proposal summary")
        from .lead_review import validate_intent_text
        validate_intent_text(proposal, "proposal", limit=200000)
        with transaction(self.store.db) as db:
            task = self.snapshot(task_id)
            if task['contract'].get('lead_review'):
                from .lead_review import archive_approved_intent
                archive_approved_intent(self.store, task)
            validate_mixed_mapping(task["contract"])
            if task["legacy_v1"]:
                raise ValueError("A legacy task needs a reviewed v2 contract before new approval")
            if task["state"] in {"COMPLETED", "CANCELLED"}:
                raise ValueError("Terminal task needs a new task")
            known = {s["id"] for s in task["contract"]["stages"]}
            stages = proposal.get("stage_ids", [])
            if not isinstance(stages, list) or not stages or not set(stages).issubset(known):
                raise ValueError("Proposal requires known stage IDs")
            payload = {k: v for k, v in task.items() if k not in {"revision", "legacy_v1"}}
            payload.update(state="AWAITING_APPROVAL", proposal=copy.deepcopy(proposal),
                           proposal_hash=hash_value({"contract_hash": task["contract_hash"], "proposal": proposal}), approved_stage_ids=[])
            self._save(payload, expected_revision)
            previous = read_record(db, "approval", task_id)
            write_record(db, "approval", task_id, None, expected_revision=previous["revision"])
            append_event(db, task_id + ":proposal:" + str(expected_revision + 1), "proposal", task_id,
                         {"proposal_hash": payload["proposal_hash"], "stage_ids": stages})
        return self.snapshot(task_id)

    def approve(self, task_id, *, proposal_hash, user_message_ref, approved_stage_ids):
        text(user_message_ref, "approval user_message_ref", limit=2000)
        with transaction(self.store.db) as db:
            task = self.snapshot(task_id)
            validate_mixed_mapping(task["contract"])
            if task["state"] in {"COMPLETED", "CANCELLED"}:
                raise ValueError("Terminal task needs a new task")
            if task["legacy_v1"] or not task["proposal_hash"] or proposal_hash != task["proposal_hash"]:
                raise ValueError("Approval hash differs from current proposal hash")
            if not isinstance(approved_stage_ids, list) or not approved_stage_ids or not set(approved_stage_ids).issubset(task["proposal"]["stage_ids"]):
                raise ValueError("Approval stage scope differs from proposal")
            stages = {s["id"]: s for s in task["contract"]["stages"]}
            for stage_id in approved_stage_ids:
                self._check_unknowns(task, stages[stage_id])
            approval = {"proposal_hash": proposal_hash, "contract_hash": task["contract_hash"],
                        "approved_stage_ids": sorted(set(approved_stage_ids)), "user_message_ref": user_message_ref, "approved_at_utc": now()}
            previous = read_record(db, "approval", task_id)
            write_record(db, "approval", task_id, approval, expected_revision=previous["revision"])
            version_key = task_id + ":" + str(previous["revision"] + 1)
            write_record(db, "approval_version", version_key, approval, expected_revision=0)
            append_event(db, task_id + ":approval:" + str(previous["revision"] + 1), "approval", task_id, approval)
            payload = {k: v for k, v in task.items() if k not in {"revision", "legacy_v1"}}
            payload.update(state="READY", approved_stage_ids=approval["approved_stage_ids"])
            self._save(payload, task["revision"])
            if task['contract'].get('lead_review'):
                from .lead_review import archive_approved_intent
                archive_approved_intent(self.store, self.snapshot(task_id))
        return self.snapshot(task_id)

    @staticmethod
    def _check_unknowns(task, stage):
        stages = task["contract"]["stages"]
        for unknown in task["contract"]["unknowns"]:
            affected = set(unknown.get("blocking_stage_ids", [])) | {
                candidate["id"] for candidate in stages if unknown["id"] in candidate.get("unknown_ids", [])}
            if not affected or not affected.issubset({candidate["id"] for candidate in stages}):
                affected = {candidate["id"] for candidate in stages}
            while True:
                expanded = affected | {candidate["id"] for candidate in stages if set(candidate.get("depends_on", [])) & affected}
                if expanded == affected:
                    break
                affected = expanded
            if unknown.get("status", "UNRESOLVED") == "UNRESOLVED" and stage["id"] in affected:
                raise ValueError("Unresolved unknown blocks stage: " + unknown["id"])

    def require_approval(self, task_id, stage_id, *, allow_terminal=False, allow_stopped=False):
        task = self.snapshot(task_id)
        if not allow_stopped and read_record(self.store.db, "stop", task_id)["payload"]:
            raise ValueError("Task is paused by a stop request")
        if task["state"] == "CANCELLED":
            raise ValueError("Task is cancelled")
        validate_mixed_mapping(task["contract"])
        if task["legacy_v1"]:
            raise ValueError("legacy_v1 has no new execution approval")
        stages = {s["id"]: s for s in task["contract"]["stages"]}
        if stage_id not in stages:
            raise ValueError("Unknown stage scope")
        approval = read_record(self.store.db, "approval", task_id)["payload"]
        if task["contract"].get("parent_task_id"):
            parent = self.require_approval(task["contract"]["parent_task_id"], task["contract"].get("parent_stage_id"), allow_terminal=allow_terminal)
            parent_stage = next(s for s in parent["contract"]["stages"] if s["id"] == task["contract"]["parent_stage_id"])
            if not _within(task["contract"]["scope"], parent_stage["scope"]):
                raise ValueError("Child scope exceeds approved parent")
            validate_child_obligations(task["contract"], parent["contract"], parent_stage, task_id)
            if task.get("parent_proposal_hash") != parent["proposal_hash"]:
                raise ValueError("Child parent approval binding is stale")
        inherited = bool(task.get("inherited_approval") and task["proposal_hash"] and not task.get("proposal"))
        if not inherited and (not approval or approval["proposal_hash"] != task["proposal_hash"] or approval["contract_hash"] != task["contract_hash"]
              or stage_id not in approval["approved_stage_ids"]):
            raise ValueError("Current stage requires explicit approval")
        self._check_unknowns(task, stages[stage_id])
        readiness = self.readiness(task_id, stage_id=stage_id)
        if readiness["untracked_gap_ids"]:
            raise ValueError("Required source gaps need explicit stage obligations: " + ", ".join(readiness["untracked_gap_ids"]))
        if readiness["required_source_gaps"]:
            raise ValueError("Unresolved source gaps block stage: " + ", ".join(gap["id"] for gap in readiness["required_source_gaps"]))
        if not allow_terminal and task["state"] in {"CANCELLED", "COMPLETED"}:
            raise ValueError("Task is terminal")
        return task

    def execution_plan(self, task_id):
        self.snapshot(task_id)
        record = read_record(self.store.db, "execution_plan", task_id)
        if record["payload"] is None:
            raise ValueError("Legacy task has no v2 execution plan")
        return {**record["payload"], "revision": record["revision"]}

    def update_execution_plan(self, task_id, plan, *, expected_revision):
        if not isinstance(plan, dict) or set(plan) != {"checks"}:
            raise ValueError("Execution plan requires checks")
        _unique(plan["checks"], "checks")
        with transaction(self.store.db) as db:
            task = self.snapshot(task_id)
            current = self.execution_plan(task_id)
            by_id = {c["id"]: c for c in plan["checks"]}
            for baseline in current["checks"]:
                if by_id.get(baseline["id"]) != baseline:
                    raise ValueError("Existing required/baseline check cannot be weakened or removed")
            for check in plan["checks"]:
                validate_evidence_spec(check, task["contract"]["profile"])
                if "scope" in check and not _within(_scope(check["scope"]), task["contract"]["scope"]):
                    raise ValueError("Additional check scope exceeds approved scope")
            payload = {"checks": copy.deepcopy(plan["checks"]), "plan_hash": hash_value(plan["checks"])}
            write_record(db, "execution_plan", task_id, payload, expected_revision=expected_revision)
        return self.execution_plan(task_id)

    def transition(self, task_id, target, *, expected_revision, reason):
        if target not in STATES:
            raise ValueError("Unknown workflow state")
        text(reason, "reason", limit=4000)
        with transaction(self.store.db) as db:
            task = self.snapshot(task_id)
            if task["legacy_v1"]:
                raise ValueError("Legacy state transitions require migration")
            if task["state"] in {"CANCELLED", "COMPLETED"} and target != task["state"]:
                raise ValueError("Terminal task cannot be reopened without a reviewed new task")
            if target == "COMPLETED":
                if any(u.get("status", "UNRESOLVED") == "UNRESOLVED" for u in task["contract"]["unknowns"]):
                    raise ValueError("Unresolved parent obligations prevent completion")
                from .gates import GateService
                gate = GateService(self.store).gate(task_id)
                plan = self.execution_plan(task_id)
                if (not gate or gate.get("status") != "APPROVED" or gate.get("contract_hash") != task["contract_hash"]
                        or gate.get("plan_hash") != plan["plan_hash"]):
                    raise ValueError("COMPLETED requires a current APPROVED gate")
            if target in {"EXECUTING", "VALIDATING", "REVIEWING", "DIAGNOSING"}:
                if not task["approved_stage_ids"] and not task["contract"].get("parent_task_id"):
                    raise ValueError("Execution requires approval")
            payload = {k: v for k, v in task.items() if k not in {"revision", "legacy_v1"}}
            payload.update(state=target, last_reason=reason)
            self._save(payload, expected_revision)
            append_event(db, task_id + ":transition:" + str(expected_revision + 1), "transition", task_id,
                         {"state": target, "reason": reason})
        return self.snapshot(task_id)
