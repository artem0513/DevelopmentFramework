"""Dated project knowledge. Append history; never infer present truth or authority."""
from datetime import datetime
import json
from pathlib import Path
import re

from .continuity import _strings, _text
from .store import now


class Knowledge:
    def __init__(self, store):
        self.store = store

    def record(self, payload):
        allowed = {"topic", "statement", "observed_at", "source_refs", "task_id", "repository", "repository_revision", "supersedes"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("Unknown knowledge fields")
        value = dict(payload)
        for key in ("topic", "statement", "observed_at"):
            _text(value.get(key), key)
        if len(value["topic"]) > 200 or len(value["statement"]) > 4000 or len(value["observed_at"]) > 80:
            raise ValueError("Use a concise topic, statement and observed_at")
        observed = datetime.fromisoformat(value["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("observed_at requires a timezone-aware date")
        refs = _strings(value.get("source_refs"), "source_refs")
        if not refs or len(refs) > 20 or any(len(ref) > 2048 for ref in refs):
            raise ValueError("Knowledge needs 1 to20 concise source references")
        task_id = value.get("task_id")
        if task_id is not None:
            _text(task_id, "task_id")
            self.store.task(task_id)
        repository, revision = value.get("repository"), value.get("repository_revision")
        if repository is not None or revision is not None:
            _text(repository, "repository")
            if not isinstance(revision, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", revision):
                raise ValueError("Repository observations require a full commit SHA")
        predecessor = value.get("supersedes")
        if predecessor is not None and (type(predecessor) is not int or predecessor <= 0):
            raise ValueError("supersedes must identify a prior claim")
        db = self.store.db
        db.execute("BEGIN IMMEDIATE")
        try:
            if predecessor is not None:
                previous = db.execute("SELECT * FROM knowledge_claims WHERE id=?", (predecessor,)).fetchone()
                if previous is None or previous["topic"] != value["topic"]:
                    raise ValueError("Superseded claim must exist in the same project and topic")
                if db.execute("SELECT 1 FROM knowledge_claims WHERE supersedes=?", (predecessor,)).fetchone():
                    raise ValueError("Claim already superseded; query and reconcile newer history")
                if observed < datetime.fromisoformat(previous["observed_at"]):
                    raise ValueError("Superseding observation cannot predate its predecessor")
            row = db.execute("INSERT INTO knowledge_claims(topic,statement,observed_at,source_refs,task_id,repository,repository_revision,supersedes,created) VALUES(?,?,?,?,?,?,?,?,?)", (
                value["topic"], value["statement"], observed.isoformat(), json.dumps(refs), task_id, repository, revision, predecessor, now()))
            claim_id = row.lastrowid
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return next(claim for claim in self.query(value["topic"])["claims"] if claim["id"] == claim_id)

    def query(self, topic=None):
        if topic is not None:
            _text(topic, "topic")
        where, args = (" WHERE k.topic=?", (topic,)) if topic is not None else ("", ())
        rows = self.store.db.execute("SELECT k.*, (SELECT id FROM knowledge_claims WHERE supersedes=k.id) AS superseded_by FROM knowledge_claims k" + where + " ORDER BY k.id", args).fetchall()
        claims = []
        for row in rows:
            claim = dict(row)
            claim["source_refs"] = json.loads(claim["source_refs"])
            claims.append(claim)
        return {"repository": str(self.store.repo), "claims": claims,
                "authority": "Historical recorded claims only; verify current code and user authority before acting"}

    def lint(self):
        claims = self.query()["claims"]
        findings, topics = [], {}
        for claim in claims:
            if claim["superseded_by"] is None:
                topics.setdefault(claim["topic"], []).append(claim["id"])
            for ref in claim["source_refs"]:
                if ref.startswith("local:"):
                    path = (self.store.repo / ref[6:]).resolve()
                    if not path.is_relative_to(self.store.repo):
                        findings.append({"kind": "out_of_scope_reference", "claim_id": claim["id"], "reference": ref})
                    elif not path.is_file():
                        findings.append({"kind": "missing_local_reference", "claim_id": claim["id"], "reference": ref})
        for topic, ids in topics.items():
            if len(ids) > 1:
                findings.append({"kind": "parallel_claims", "topic": topic, "claim_ids": ids,
                                 "meaning": "Review unsuperseded claims; coexistence does not prove a semantic contradiction"})
        return {"status": "UNCONFIGURED" if not claims else "REVIEW_NEEDED" if findings else "NO_STRUCTURAL_FINDINGS", "findings": findings,
                "semantic_validation": False, "limitation": "Local link structure only; external sources not fetched and current truth not inferred"}
