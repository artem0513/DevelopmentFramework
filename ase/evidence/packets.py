"""Read-only evidence packet construction with explicit source coverage."""
import hashlib
import json
from pathlib import Path
from ..gates import source_snapshot, source_resources, stage_checks
from ..native_paths import logical_path, state_io
from ..runner import verify_receipt
from ..state import read_record, encode

from ..execution.ports import PacketContext


class PacketService:
    def __init__(self, context: PacketContext, *, target):
        self.context = context
        self.target = target

    def packet(self, task_id, stage_id=None):
        context = self.context
        from ..source_coverage import source_availability, availability_gaps, source_bytes, bound_representation, read_persisted_intake
        task = context.tasks.snapshot(task_id)
        row = context.store.db.execute("SELECT payload FROM task_discoveries WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        discovery = json.loads(row[0]) if row else None
        intake_state = read_persisted_intake(discovery)
        availability = source_availability(context.store, task["contract"], discovery, stage_id)
        policies = {item["path"]: item for item in availability}
        representations = {item["artifact_path"]: item for item in (discovery or {}).get("representations", [])}
        plan = context.tasks.execution_plan(task_id)
        stage = next((item for item in task["contract"]["stages"] if item["id"] == stage_id), None)
        if stage_id is not None and stage is None:
            raise ValueError("Unknown source packet stage")
        must_review = {Path(item["path"]).as_posix() for item in stage_checks(task["contract"], plan["checks"], stage_id)
                       if item["kind"] == "artifact" and item.get("required", True)}

        def source_policy(path):
            matching = [item for resource, item in policies.items() if Path(path).is_relative_to(Path(resource))]
            return max(matching, key=lambda item: len(Path(item["path"]).parts)) if matching else None

        def coverage_required(path):
            if body_omission(path) is not None:
                return False
            policy = source_policy(path)
            return path in must_review or policy is None or (policy["relevant"] and (policy["required"] or policy["role"] in {"output", "workspace", "representation"}))

        def direct_body_omission(path):
            if path in must_review:
                return None
            policy = source_policy(path)
            if policy is not None:
                if not policy["relevant"]:
                    return "OMITTED_IRRELEVANT_STAGE"
                if policy["role"] == "input" and not policy["required"]:
                    return "OMITTED_OPTIONAL_INPUT"
            return None

        def body_omission(path):
            if path in must_review:
                return None
            owners = [artifact for artifact, declaration in representations.items() if declaration["path"] == path]
            if owners:
                policy = source_policy(path)
                if policy is not None and policy["role"] != "representation" and direct_body_omission(path) is None:
                    return None  # Independently required as a source or output.
                decisions = [direct_body_omission(artifact) for artifact in owners]
                if all(decisions):
                    return "OMITTED_OPTIONAL_INPUT" if "OMITTED_OPTIONAL_INPUT" in decisions else "OMITTED_IRRELEVANT_STAGE"
                return None  # At least one included artifact needs this body.
            return direct_body_omission(path)

        snapshot = source_snapshot(context.store, task["contract"], task_id=task_id)
        baseline = read_record(context.store.db, "baseline", task_id)["payload"]
        changes = None
        if baseline:
            paths = set(baseline["files"]) | set(snapshot["files"])
            changed = sorted(p for p in paths if baseline["files"].get(p) != snapshot["files"].get(p))
            if len(changed) > 1000:
                raise ValueError("Source change inventory exceeds the bounded review packet")
            unchanged = sorted(p for p in paths if baseline["files"].get(p) == snapshot["files"].get(p))
            changes = {"baseline_fingerprint": baseline["fingerprint"], "current_fingerprint": snapshot["fingerprint"],
                       "baseline_dirty": baseline.get("dirty"), "coverage": "Actual content/mode hashes from baseline and current source observations; software covers tracked and nonignored untracked files plus declared dependencies",
                       "changes": [{"path": p, "before_sha256": baseline["files"].get(p), "after_sha256": snapshot["files"].get(p)} for p in changed],
                       "unchanged_paths": unchanged[:1000], "unchanged_path_count": len(unchanged), "unchanged_paths_truncated": len(unchanged) > 1000}
            review_scope = stage["scope"] if stage is not None else task["contract"]["scope"]
            if set(review_scope["effects"]) & {"file_write", "command"}:
                must_review.update(path for path in changed if any(Path(path).is_relative_to(Path(resource)) for resource in review_scope["resources"]))
        must_review.update(item["path"] for artifact, item in representations.items() if artifact in must_review)
        files = []
        missing = [gap["id"] + ": " + gap["text"] for gap in [*availability_gaps(availability), *intake_state["compatibility_gaps"]]]
        for path, observation in snapshot.get("unavailable", {}).items():
            availability.append({"path": path, **observation, "reason": "Source snapshot could not read this declared resource"})
            if coverage_required(path):
                missing.append(path + ": source snapshot unavailable (" + observation["error"] + ")")
        representation_metadata = []
        for artifact, declaration in representations.items():
            omission = body_omission(artifact)
            if omission:
                artifact_hash = snapshot["files"].get(artifact, "").removesuffix(":x")
                readable_hash = snapshot["files"].get(declaration["path"], "").removesuffix(":x")
                current = artifact_hash == declaration["artifact_sha256"] and readable_hash == declaration["sha256"]
                representation_metadata.append({**declaration, "body_status": omission,
                                                "observed_artifact_sha256": artifact_hash or None, "observed_representation_sha256": readable_hash or None,
                                                "integrity": "CURRENT_SNAPSHOT_HASHES" if current else "STALE_OR_UNAVAILABLE_SNAPSHOT_BINDING",
                                                "limitation": "Artifact/representation bodies were omitted from review; the declared binding and current source hashes remain visible"})
        omitted_sources = []
        selected = set()
        for resource in source_resources(context.store, task["contract"], task_id):
            candidate = context.store.repo / resource
            if candidate.resolve().is_relative_to(context.store.repo.resolve()):
                if candidate.is_dir():
                    selected.update(p for p in snapshot["files"] if (context.store.repo / p).is_relative_to(candidate.resolve()))
                else:
                    selected.add(resource.replace("\\", "/"))
        for path in sorted(selected):
            candidate = context.store.repo / path
            try:
                candidate = self.target(context.store.repo.resolve(), path)
                if any(p.lower() == ".git" for p in candidate.resolve().relative_to(context.store.repo.resolve()).parts):
                    raise ValueError("Private Git data is not a source packet")
                if not candidate.exists():
                    continue
                omission = body_omission(path)
                if omission:
                    policy = source_policy(path)
                    observed_hash = snapshot["files"].get(path)
                    omitted_sources.append({"path": path, "sha256": observed_hash,
                                            "status": "AVAILABLE" if observed_hash is not None else "UNAVAILABLE",
                                            "body_status": omission, "required": policy["required"], "relevant": policy["relevant"],
                                            "blocking_stage_ids": policy["blocking_stage_ids"], "reason": policy["reason"],
                                            "limitation": "Body was not supplied or reviewed in this packet; current source hashes and declared stage impact remain visible"})
                    continue
                if path in representations:
                    represented = bound_representation(context.store.repo, representations[path])
                    if not any(item["path"] == represented["path"] for item in files):
                        files.append(represented)
                    representation_metadata.append({**representations[path], "integrity": "Current artifact and readable representation bytes match both declared hashes",
                                                    "limitation": "Representation adequacy requires independent domain judgment; native binary bytes are not interpreted by this transport"})
                    continue
                data = source_bytes(context.store.repo, path)
                content = data.decode("utf-8")
                if "\0" in content:
                    raise ValueError("source contains binary NUL bytes")
                if not any(item["path"] == path for item in files):
                    files.append({"path": path, "content": content, "sha256": hashlib.sha256(data).hexdigest()})
            except (UnicodeError, OSError, ValueError) as ex:
                if coverage_required(path):
                    missing.append(path + ": " + str(ex))
                else:
                    availability.append({"path": path, "status": "UNCOVERED", "required": False,
                                         "limitation": str(ex), "reason": "Optional or outside the current stage's source requirement"})
        historical = read_record(context.store.db, "baseline_implementation", task_id)["payload"]
        if historical and changes:
            try:
                if not Path(historical["artifact_root"]).resolve().is_relative_to(context.store.root.resolve()):
                    raise ValueError("Historical source capture escapes project state")
                verify_receipt(historical, require_current_policy=False)
                original = json.loads(Path(historical["artifacts"]["request"]["path"]).read_bytes())
                changed_paths = {entry["path"] for entry in changes["changes"]}
                for before in original["source_packet"]["files"]:
                    if before["path"] in changed_paths and body_omission(before["path"]) is None:
                        files.append({**before, "path": "_ase_before/" + before["path"]})
            except (ValueError, OSError, KeyError, TypeError) as ex:
                missing.append("Historical source capture unavailable: " + str(ex))
        stage_history, history_missing = context._stage_history(task)
        missing.extend(history_missing)
        from ..action_evidence import action_packet_evidence
        action_files, action_missing = action_packet_evidence(context.store, task, plan, snapshot, stage_id=stage_id)
        files.extend(action_files)
        missing.extend(action_missing)
        from ..delegations import DelegationRegistry
        try:
            delegation_evidence = DelegationRegistry(context.store).packet_evidence(task_id, stage_id=stage_id)
        except (ValueError, OSError, KeyError, TypeError) as ex:
            from ..core import redact
            reason = 'Native delegation evidence unavailable: ' + redact(ex)
            missing.append(reason)
            delegation_evidence = {'status': 'UNAVAILABLE', 'reason': reason}
        for name, data in (("contract", task["contract"]), ("execution_plan", plan), ("source_changes", changes), ("stage_history", stage_history),
                           ("discovery", discovery), ("intake_validation", intake_state["validation"]), ("source_availability", availability), ("artifact_representations", representation_metadata), ("omitted_sources", omitted_sources),
                           ('delegations', delegation_evidence), ("actual_checks", read_record(context.store.db, "checks", task_id)["payload"])):
            content = encode(data)
            files.append({"path": "_ase_context/" + name + ".json", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest()})
        check = read_record(context.store.db, "checks", task_id)["payload"]
        for result in (check or {}).get("results", []):
            if result.get("log"):
                try:
                    log = logical_path(result["log"])
                    if not log.is_relative_to(context.store.root.resolve()):
                        raise ValueError("Log is outside project state")
                    physical = state_io(log)
                    if not physical.is_file():
                        raise ValueError("Log is unavailable")
                    content = physical.read_bytes().decode("utf-8", errors="replace")
                except (OSError, ValueError):
                    missing.append("Actual check log unavailable: " + result["id"])
                    continue
                files.append({"path": "_ase_context/logs/" + result["id"] + ".txt", "content": content,
                              "sha256": hashlib.sha256(content.encode()).hexdigest()})
        packet = {"requirements": [task["contract"]["request"], *[ac["id"] + ": " + ac["expected"] for ac in task["contract"]["acceptance"]]],
                "files": files, "coverage": {"scope": "Current stage source plus task contract and actual check logs; known irrelevant/optional bodies are explicitly disclosed in omitted_sources; undeclared dependencies are not proven complete",
                                               "included": [f["path"] for f in files], "missing": missing,
                                               "omitted": [item["path"] for item in omitted_sources]}}
        from ..protocol import validate_packet
        return validate_packet(packet)
