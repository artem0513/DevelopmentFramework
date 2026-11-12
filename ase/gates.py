"""Current-source evidence gate; semantic judgments remain separate from proof."""
import fnmatch
import hashlib
import json
from pathlib import Path
import re

from .profiles import evaluate_contract_profiles, has_software, stage_evidence_specs, validate_mixed_mapping
from .runner import verify_receipt
from .state import hash_value, read_record, transaction, write_record
from .store import now
from .tasks import TaskService, _within, validate_child_obligations


def evaluate_gate(contract, current, receipts, judgments):
    rejected, blocked = [], []
    if current.get("contract_hash") != hash_value(contract):
        rejected.append("Current contract hash mismatch")
    checks = [r for r in receipts if r.get("kind") == "checks"]
    if len(checks) != 1:
        blocked.append("Exactly one current check bundle required")
        check = {}
    else:
        check = checks[0]
        for key in ("fingerprint", "contract_hash", "plan_hash"):
            if check.get(key) != current.get(key):
                rejected.append("Stale check evidence: " + key)
        if check.get("source_changed_during_checks"):
            rejected.append("Source changed during checks")
    specs = current.get("checks", contract.get("evidence", []))
    profile = evaluate_contract_profiles(contract, specs, check.get("results", []), stage_id=current.get("stage_id"))
    rejected.extend(profile["rejected_reasons"])
    blocked.extend(profile["blocked_reasons"])
    expected_bindings = {"fingerprint": current.get("fingerprint"), "contract_sha256": current.get("contract_hash"),
                         "proposal_sha256": current.get("proposal_hash"), "plan_sha256": current.get("plan_hash"),
                         "check_sha256": current.get("check_hash")}
    if current.get("workspace_id"):
        expected_bindings["workspace_id"] = current["workspace_id"]
    roles, execution_ids, session_ids = {}, set(), set()
    for role in ("ac", "review"):
        matches = [r for r in receipts if r.get("role") == role]
        if len(matches) != 1:
            blocked.append("Independent " + role + " session missing or duplicated")
            continue
        receipt = matches[0]
        if receipt.get("execution_id") in execution_ids or receipt.get("execution_id") == current.get("implementation_execution_id"):
            rejected.append("Independent roles reused the same execution")
            continue
        execution_ids.add(receipt.get("execution_id"))
        try:
            verified = verify_receipt(receipt, expected_bindings=expected_bindings)
            if verified["evidence_kind"] != "independent_cli":
                blocked.append("Protocol fixtures are not independent semantic approval: " + role)
                continue
            session_id = verified["result"].get("session_id")
            if session_id and session_id in session_ids:
                rejected.append("Independent roles reused the same provider session")
                continue
            session_ids.add(session_id)
            roles[role] = verified["result"]["payload"]
        except (ValueError, OSError, KeyError, TypeError) as ex:
            blocked.append("Independent " + role + " receipt invalid: " + str(ex))
    criteria = roles.get("ac", {}).get("criteria", [])
    ids = [ac.get("id") for ac in criteria if isinstance(ac, dict)]
    if len(ids) != len(criteria) or len(ids) != len(set(ids)):
        rejected.append("Duplicate or malformed independent AC results")
    unexpected = set(ids) - {ac["id"] for ac in contract.get("acceptance", [])}
    if unexpected:
        blocked.append("Independent verifier reported uncontracted requirements; reconcile coverage: " + ", ".join(sorted(unexpected)))
    selected_stage = next((s for s in contract.get("stages", []) if s["id"] == current.get("stage_id")), None)
    for ac in contract.get("acceptance", []):
        if selected_stage is not None and ac["id"] not in selected_stage["acceptance_ids"]:
            continue
        if not ac.get("mandatory", True):
            continue
        linked = ac.get("evidence_ids", ac.get("commands", []))
        if not linked:
            blocked.append("Mandatory AC has no executable/evaluable evidence mapping: " + ac["id"])
        for evidence_id in linked:
            result = profile["by_id"].get(evidence_id)
            if not result or result["status"] != "PASS":
                (rejected if result and result["status"] == "FAIL" else blocked).append("Mandatory AC proof missing/failed: " + ac["id"] + "/" + evidence_id)
        matches = [r for r in criteria if isinstance(r, dict) and r.get("id") == ac["id"]]
        if len(matches) != 1:
            blocked.append("Independent mandatory AC result missing: " + ac["id"])
            continue
        result = matches[0]
        if result.get("expected") != ac["expected"]:
            rejected.append("Verifier changed expected behavior: " + ac["id"])
        if result.get("status") == "FAIL":
            rejected.append("Mandatory AC failed: " + ac["id"])
        elif (result.get("status") != "PASS" or not result.get("evidence") or not result.get("method") or result.get("missing_coverage")):
            blocked.append("Mandatory AC unverified: " + ac["id"])
    for finding in roles.get("review", {}).get("findings", []):
        if not isinstance(finding, dict) or finding.get("severity") not in {"P0", "P1", "P2", "P3"}:
            blocked.append("Malformed independent review finding")
        elif finding["severity"] in {"P0", "P1", "P2"}:
            rejected.append(finding["severity"] + ": " + str(finding.get("message", "Blocking review finding")))
    if current.get("protected_changed"):
        rejected.append("Unauthorized protected file changes: " + ", ".join(current["protected_changed"]))
    if contract.get("risk") == "HIGH" and not current.get("high_risk_approved"):
        blocked.append("HIGH risk requires current explicit approval")
    for unknown in contract.get("unknowns", []):
        if selected_stage is not None and selected_stage["id"] not in unknown.get("blocking_stage_ids", []) and unknown["id"] not in selected_stage.get("unknown_ids", []):
            continue
        if unknown.get("status", "UNRESOLVED") == "UNRESOLVED":
            blocked.append("Unresolved parent obligation: " + unknown["id"])
    return {"status": "REJECTED" if rejected else "BLOCKED" if blocked else "APPROVED",
            "rejected_reasons": list(dict.fromkeys(rejected)), "blocked_reasons": list(dict.fromkeys(blocked)),
            "reasons": list(dict.fromkeys(rejected + blocked)), "contract_hash": current.get("contract_hash"),
            "proposal_hash": current.get("proposal_hash"), "plan_hash": current.get("plan_hash"),
            "fingerprint": current.get("fingerprint"), "profile_evidence": profile, "timestamp": now()}


def stage_checks(contract, checks, stage_id=None):
    return stage_evidence_specs(contract, checks, stage_id)


def source_resources(store, contract, task_id=None):
    """The same explicit source/dependency boundaries for hashes and packets."""
    resources = list(contract["scope"]["resources"])
    if task_id is not None:
        row = store.db.execute("SELECT payload FROM task_discoveries WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        if row:
            resources.extend(r["path"] for r in json.loads(row[0])["resources"] if r.get("status") == "INSPECTED")
    return list(dict.fromkeys(resources))


def _directory_sources(root, directory):
    """Bound generic directory inventory without traversing private Git data."""
    pending, count = [directory], 0
    while pending:
        for candidate in pending.pop().iterdir():
            if candidate.name.lower() == ".git":
                continue
            count += 1
            if count > 1000:
                raise ValueError("Scoped source inventory exceeds limit")
            resolved = candidate.resolve()
            if (candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)()
                    or not resolved.is_relative_to(root)
                    or any(p.lower() == ".git" for p in resolved.relative_to(root).parts)):
                raise ValueError("Source escapes workspace")
            if candidate.is_dir():
                pending.append(candidate)
            elif candidate.is_file():
                yield candidate


def source_snapshot(store, contract, task_id=None):
    base = None
    if has_software(contract):
        from .inspection import safe_snapshot
        base = safe_snapshot(store.repo)
    files, missing = dict(base["files"]) if base else {}, []
    root = store.repo.resolve()
    resources = source_resources(store, contract, task_id)
    dependencies = {}
    for resource in resources:
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", resource):
            missing.append(resource)
            continue
        path = Path(resource)
        if any(part.lower() == ".git" for part in path.parts):
            raise ValueError("Private Git data is not project source")
        if path.is_absolute():
            path = path.resolve()
        else:
            path = (root / path).resolve()
        if not path.is_relative_to(root):
            # External resource identifiers are handled by actual probe evidence.
            missing.append(resource)
            continue
        if any(part.lower() == ".git" for part in path.relative_to(root).parts):
            raise ValueError("Private Git data is not project source")
        if path.is_dir():
            # Git already supplies tracked/nonignored files. Directory discovery
            # must not add generated or ignored trees (including check output).
            # An explicitly named ignored dependency file remains supported.
            candidates = ([root / name for name in base["files"] if (root / name).is_relative_to(path)]
                          if base is not None else sorted(_directory_sources(root, path)))
        else:
            candidates = [path]
        if len(candidates) > 1000:
            raise ValueError("Scoped source inventory exceeds limit")
        for candidate in candidates:
            if (candidate.is_symlink() or not candidate.resolve().is_relative_to(root)
                    or any(p.lower() == ".git" for p in candidate.resolve().relative_to(root).parts)):
                raise ValueError("Source escapes workspace")
            if candidate.is_file():
                relative = str(candidate.relative_to(root)).replace("\\", "/")
                observed = hashlib.sha256(candidate.read_bytes()).hexdigest()
                if relative not in files:
                    dependencies[relative] = observed
                    files[relative] = observed
                elif base is None:
                    files[relative] = observed
    if base:
        return {**base, "fingerprint": hash_value({"base": base["fingerprint"], "dependencies": dependencies}) if dependencies else base["fingerprint"], "files": files}
    return {"fingerprint": hash_value({"files": files, "external_resources": missing}), "files": files,
            "workspace_id": store.workspace_id, "timestamp": now()}


class GateService:
    def __init__(self, store):
        self.store = store

    def required_child_blockers(self, task_id, stage_id=None):
        """Fresh pre-mutation child readiness; never launches a child controller."""
        task = TaskService(self.store).snapshot(task_id)
        if task["legacy_v1"]:
            return ["Required child readiness requires a reviewed v2 contract"]
        contract = task["contract"]
        if stage_id is not None and not any(stage["id"] == stage_id for stage in contract["stages"]):
            raise ValueError("Unknown required-child stage")
        try:
            validate_mixed_mapping(contract)
        except (ValueError, TypeError, KeyError) as error:
            return ["MIXED_PROFILE_UNMAPPED: " + str(error)]
        return self._required_children(task_id, contract, stage_id)

    def _required_children(self, task_id, contract, stage_id):
        blocked = []
        for stage in contract["stages"]:
            child_id = stage.get("required_child_task_id")
            if not child_id or (stage_id is not None and stage["id"] != stage_id):
                continue
            child = read_record(self.store.db, "task", child_id)["payload"]
            if child is None:
                blocked.append("Required child missing: " + child_id)
                continue
            c = child["contract"]
            if c.get("parent_task_id") != task_id or c.get("parent_stage_id") != stage["id"] or not _within(c["scope"], stage["scope"]):
                blocked.append("Required child parent/stage/scope mapping differs: " + child_id)
                continue
            try:
                validate_child_obligations(c, contract, stage, child_id)
            except ValueError as error:
                blocked.append("Required child domain/acceptance mapping invalid: " + str(error))
                continue
            if child["state"] != "COMPLETED":
                blocked.append("Required child incomplete: " + child_id)
                continue
            proof = self.gate(child_id)
            if proof["status"] != "APPROVED":
                blocked.append("Required child evidence is not currently approved: " + child_id)
        return blocked

    def gate(self, task_id, stage_id=None):
        tasks = TaskService(self.store)
        task = tasks.snapshot(task_id)
        if task["legacy_v1"]:
            return {"status": "BLOCKED", "legacy_v1": True,
                    "blocked_reasons": ["Legacy history does not grant v2 execution authority; revise and approve a v2 contract"],
                    "rejected_reasons": [], "reasons": ["Legacy task requires reviewed v2 migration"]}
        try:
            validate_mixed_mapping(task["contract"])
        except (ValueError, TypeError, KeyError) as error:
            return {"status": "BLOCKED", "blocker": "MIXED_PROFILE_UNMAPPED", "blocked_reasons": [str(error)],
                    "rejected_reasons": [], "reasons": [str(error)]}
        plan = tasks.execution_plan(task_id)
        stage = next((s for s in task["contract"]["stages"] if s["id"] == stage_id), None)
        if stage_id is not None and stage is None:
            raise ValueError("Unknown gate stage")
        source = source_snapshot(self.store, task["contract"], task_id=task_id)
        check = read_record(self.store.db, "checks", task_id)["payload"]
        current = {"contract_hash": task["contract_hash"], "proposal_hash": task["proposal_hash"], "plan_hash": plan["plan_hash"],
                   "fingerprint": source["fingerprint"], "check_hash": hash_value(check) if check else "missing",
                   "checks": plan["checks"], "workspace_id": self.store.workspace_id}
        if stage is not None:
            current.update(stage_id=stage_id, checks=stage_checks(task["contract"], plan["checks"], stage_id))
        implementation = read_record(self.store.db, "implementation", task_id)["payload"]
        current["implementation_execution_id"] = (implementation or {}).get("execution_id")
        receipts = [check] if check else []
        for role in ("ac", "review"):
            record = read_record(self.store.db, "verification", task_id + ":" + role)["payload"]
            if record:
                path = Path(record.get("artifact_root", "")).resolve()
                if not path.is_relative_to(self.store.root.resolve()):
                    continue
                receipts.append(record)
        baseline = read_record(self.store.db, "baseline", task_id)["payload"]
        if has_software(task["contract"]) and baseline:
            from .core import PROTECTED
            patterns = [*PROTECTED, *task["contract"].get("protected_paths", [])]
            changed = [p for p in set(baseline["files"]) | set(source["files"]) if baseline["files"].get(p) != source["files"].get(p)]
            authorization = read_record(self.store.db, "protected_approval", task_id)["payload"]
            allowed = authorization.get("paths", []) if authorization and authorization.get("fingerprint") == source["fingerprint"] and authorization.get("contract_hash") == task["contract_hash"] else []
            current["protected_changed"] = [p for p in changed if p not in allowed and any(fnmatch.fnmatchcase(p, pattern) for pattern in patterns)]
        high = read_record(self.store.db, "high_risk_approval", task_id)["payload"]
        current["high_risk_approved"] = bool(high and high.get("fingerprint") == source["fingerprint"] and high.get("contract_hash") == task["contract_hash"])
        result = evaluate_gate(task["contract"], current, receipts, [])
        extra = []
        extra.extend(self._required_children(task_id, task["contract"], stage_id))
        approval = read_record(self.store.db, "approval", task_id)["payload"]
        if not task["contract"].get("parent_task_id") and (not approval or approval.get("proposal_hash") != task["proposal_hash"] or approval.get("contract_hash") != task["contract_hash"]):
            extra.append("Current proposal approval missing")
        elif stage_id is not None:
            try:
                tasks.require_approval(task_id, stage_id)
            except ValueError as ex:
                extra.append(str(ex))
        if task["contract"].get("parent_task_id"):
            try:
                tasks.require_approval(task_id, stage_id or task["contract"]["stages"][0]["id"], allow_terminal=True)
            except ValueError as ex:
                extra.append("Inherited parent approval invalid: " + str(ex))
        specs = {s["id"]: s for s in current["checks"]}
        access_cache = {}

        def access_blockers(access, captured, label):
            from .credentials import CredentialVault
            key = (access["credential_ref"], access["target"])
            if key not in access_cache:
                try:
                    metadata = CredentialVault(self.store.state_home).metadata(*key, timeout=1)
                    access_cache[key] = {"credential_ref": metadata["id"], "target": metadata["target"], "revision": metadata["revision"]}
                except (ValueError, OSError):
                    access_cache[key] = None
            expected = access_cache[key]
            return [] if expected is not None and captured == expected else ["Credential evidence is missing or stale: " + label]

        proofs = {proof.get("id"): proof for proof in (check or {}).get("results", [])}
        for spec in current["checks"]:
            if "access" in spec:
                extra.extend(access_blockers(spec["access"], proofs.get(spec["id"], {}).get("access"), spec["id"]))
        for proof in (check or {}).get("results", []):
            spec = specs.get(proof.get("id"))
            if not spec:
                continue
            if spec["kind"] == "artifact":
                declared = (self.store.repo / spec["path"]).resolve()
                captured = Path(proof.get("path", "")).resolve()
                if declared != captured or not captured.is_relative_to(self.store.repo.resolve()):
                    extra.append("Artifact path differs from approved plan: " + spec["id"])
            elif spec["kind"] != "manual":
                log = Path(proof.get("log", "")).resolve()
                if not log.is_relative_to((self.store.root / "tasks" / task_id / "checks").resolve()) or Path(proof.get("cwd", "")).resolve() != self.store.repo.resolve():
                    extra.append("Command evidence outside selected task/workspace: " + spec["id"])
        if stage_id is None:
            for s in task["contract"]["stages"]:
                done = read_record(self.store.db, "stage", task_id + ":" + s["id"])["payload"]
                if not done or done.get("state") != "COMPLETED" or done.get("contract_hash") != task["contract_hash"] or done.get("proposal_hash") != task["proposal_hash"]:
                    extra.append("Stage incomplete: " + s["id"])
            for row in self.store.db.execute("SELECT key,payload FROM runtime_records WHERE kind='task'"):
                child = json.loads(row["payload"])
                if child and child["contract"].get("parent_task_id") == task_id and child["state"] != "COMPLETED":
                    extra.append("Child obligation incomplete: " + row["key"])
        for row in self.store.db.execute("SELECT payload FROM runtime_records WHERE kind='operation'"):
            operation = json.loads(row[0])
            if (operation and operation.get("task_id") == task_id
                    and (stage_id is None or operation.get("stage_id") == stage_id)
                    and operation.get("state") not in {"APPLIED", "ABSENT"}):
                extra.append("Uncertain external operation: " + operation["action_id"])
        for declared_stage in task["contract"]["stages"]:
            if stage_id is not None and declared_stage["id"] != stage_id:
                continue
            for action in declared_stage.get("actions", []):
                action_id = hash_value({"task": task_id, "stage": declared_stage["id"], "action": action["id"]})
                operation = read_record(self.store.db, "operation", action_id)["payload"]
                if (not operation or operation.get("intent", {}).get("action") != action
                        or operation.get("intent", {}).get("project_id") != self.store.project_id
                        or operation.get("intent", {}).get("workspace_id") != self.store.workspace_id):
                    extra.append("Action evidence is missing or stale: " + action["id"])
                elif "access" in action:
                    extra.extend(access_blockers(action["access"], operation.get("observation", {}).get("access"), "action-" + action["id"]))
                    observation = operation.get("observation", {})
                    if operation.get("state") != "APPLIED" or observation.get("exists") is not True:
                        extra.append("Action postcondition is not established: " + action["id"])
                    try:
                        path, expected_hash = observation["proof_ref"].rsplit("#sha256=", 1)
                        path = Path(path)
                        required = self.store.root / "tasks" / task_id / "actions" / action_id
                        if (path.is_symlink() or not path.resolve().is_relative_to(required.resolve())
                                or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash):
                            raise ValueError("Invalid observation proof")
                    except (KeyError, ValueError, OSError, TypeError):
                        extra.append("Action observation proof is missing or invalid: " + action["id"])
        if extra:
            result["blocked_reasons"] = list(dict.fromkeys(result["blocked_reasons"] + extra))
            result["reasons"] = list(dict.fromkeys(result["reasons"] + extra))
            if result["status"] == "APPROVED":
                result["status"] = "BLOCKED"
        if stage_id is not None:
            result["stage_id"] = stage_id
        with transaction(self.store.db) as db:
            key = task_id if stage_id is None else task_id + ":" + stage_id
            old = read_record(db, "gate", key)
            write_record(db, "gate", key, result, expected_revision=old["revision"])
        return result
