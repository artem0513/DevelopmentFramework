"""Explicit v2 software publication with current evidence and preserved hooks."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import parse_qsl, urlsplit
import uuid

from .core import SECRET, execute, redact
from .gates import GateService, source_snapshot
from .inspection import safe_git, trusted_git
from .journal import Journal
from .projects import _safe_state
from .state import read_record, text, transaction, write_record
from .store import now
from .tasks import TaskService
from .workflow import WorkflowController, _exclusive


class Publisher:
    def __init__(self, store):
        self.store = store
        self.tasks = TaskService(store)

    @staticmethod
    def _remote(remote):
        if not isinstance(remote, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", remote):
            raise ValueError("Invalid publishing remote name")
        return remote

    def _repository(self):
        top = Path(safe_git(self.store.repo, "rev-parse", "--show-toplevel")).resolve()
        if top != self.store.repo.resolve():
            raise ValueError("Publishing requires the selected Git repository root")

    def _argv(self, *args):
        # Pin Git, but preserve the repository's actual hooks and credential
        # helper policy. Never add --no-verify or override core.hooksPath.
        return [trusted_git(), "-C", str(self.store.repo), "-c", "safe.directory=" + str(self.store.repo), *args]

    @staticmethod
    def _environment():
        # Ambient selection/configuration can override -C and redirect Git to
        # another repository. On-disk user/repository configuration, hooks and
        # credential helpers remain active; process-supplied GIT_* overrides do
        # not belong to this selected workspace's publishing authorization.
        return {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}

    def _git(self, *args, check=True, strip=True):
        result = subprocess.run(self._argv(*args), cwd=self.store.repo, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=60, env=self._environment())
        if check and result.returncode:
            raise ValueError("Git " + args[0] + " failed: " + redact(result.stderr.decode("utf-8", "replace").strip()))
        output = result.stdout.decode("utf-8", "replace")
        return output.strip() if strip else output

    def _urls(self, remote):
        urls = self._git("remote", "get-url", "--push", "--all", remote, check=False).splitlines()
        if len(urls) != 1:
            raise ValueError("Publishing supports exactly one push URL; inspect all configured destinations")
        url = text(urls[0], "push URL", limit=2048)
        parsed = urlsplit(url)
        sensitive = re.compile(r"(?i)(?:^|[-_])(?:token|secret|password|credential|authorization|signature|api[-_]?key)(?:$|[-_])|^sig$")
        if url.startswith("-") or parsed.username is not None or any(sensitive.search(key) for key, _ in parse_qsl(parsed.query, keep_blank_values=True)):
            raise ValueError("Push URL must be credential-free")
        return urls

    def _authorization(self, remote):
        return read_record(self.store.db, "publishing_authorization", self.store.workspace_id + ":" + remote)

    def _authorized(self, remote, url):
        value = self._authorization(remote)["payload"] or {}
        return (value.get("url") == url and value.get("project_id") == self.store.project_id
                and value.get("workspace_id") == self.store.workspace_id)

    def authorize(self, remote="origin", disable=False):
        remote = self._remote(remote)
        if type(disable) is not bool:
            raise ValueError("disable must be boolean")
        self._repository()
        url = None if disable else self._urls(remote)[0]
        with transaction(self.store.db) as db:
            old = self._authorization(remote)
            value = None if disable else {"url": url, "project_id": self.store.project_id,
                "workspace_id": self.store.workspace_id, "authorized_at_utc": now()}
            write_record(db, "publishing_authorization", self.store.workspace_id + ":" + remote,
                         value, expected_revision=old["revision"])
        return {"remote": remote, "enabled": not disable,
                "note": "Explicit workspace-scoped exact push URL authorization; publication remains dry-run unless executed"}

    @staticmethod
    def _failure(status, reason, **extra):
        return {"status": status, "published": False, "reasons": [reason], **extra}

    def _secret_check(self, baseline, current):
        for name, version in current["files"].items():
            if baseline["files"].get(name) == version:
                continue
            path = self.store.repo / name
            if path.is_file() and (path.name.lower() in {".env", "id_rsa", "id_ed25519"}
                                  or path.suffix.lower() in {".pfx", ".p12", ".pem", ".key"}):
                return "Potential credential file: " + name
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
                return "Changed source is a redirected or shared file: " + name
            if path.is_file() and path.stat().st_size < 2_000_000 and SECRET.search(path.read_text(encoding="utf-8", errors="replace")):
                return "Potential secret in changed file: " + name
        return None

    def _publication_paths(self, task, baseline, current):
        """Account for worktree, index and every new commit, not just net bytes.

        Remote authorization names a destination; it does not widen the task's
        approved resources or authorize publishing unrelated local history.
        """
        base = baseline.get("commit")
        if not isinstance(base, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", base):
            raise ValueError("A full baseline commit is required to establish publication ownership")
        head = self._git("rev-parse", "HEAD")
        try:
            self._git("merge-base", "--is-ancestor", base, head)
        except ValueError:
            raise ValueError("Current HEAD diverged from the task baseline; publication ownership is unknown") from None

        def paths(*args):
            return {path for path in self._git(*args, strip=False).split("\0") if path}

        staged = paths("diff", "--no-ext-diff", "--no-textconv", "--cached", "--name-only", "--no-renames", "-z")
        stage_paths = staged | paths("diff", "--no-ext-diff", "--no-textconv", "--name-only", "--no-renames", "-z", "HEAD")
        stage_paths |= paths("ls-files", "--others", "--exclude-standard", "-z")
        changed = {path for path in set(baseline["files"]) | set(current["files"])
                   if baseline["files"].get(path) != current["files"].get(path)} | stage_paths
        commits = self._git("rev-list", base + ".." + head).splitlines()
        if len(commits) > 1000:
            raise ValueError("Publication history exceeds the bounded task review scope")
        for commit in commits:
            changed |= paths("diff-tree", "--no-commit-id", "--name-only", "--no-renames", "-r", "-m", "-z", commit)
        if changed and "file_write" not in task["contract"]["scope"]["effects"]:
            raise ValueError("Source publication changes require the approved file_write effect")
        root = self.store.repo.resolve()

        def canonical(path):
            relative = Path(path)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Publishing scope paths must stay relative to the selected repository")
            resolved = (root / relative).resolve()
            if not resolved.is_relative_to(root) or any(part.lower() == ".git" for part in resolved.relative_to(root).parts):
                raise ValueError("Publishing scope escapes the selected repository")
            return resolved

        known = {canonical(path) for path in set(baseline["files"]) | set(current["files"]) | changed}
        baseline_files = {canonical(path) for path in baseline["files"]}
        boundaries = []
        for resource in task["contract"]["scope"]["resources"]:
            boundary = canonical(resource)
            directory = boundary not in baseline_files and (boundary.is_dir() or any(path != boundary and path.is_relative_to(boundary) for path in known))
            boundaries.append((boundary, directory))
        outside = [path for path in sorted(changed) if not any(canonical(path) == boundary or
                   (directory and canonical(path).is_relative_to(boundary)) for boundary, directory in boundaries)]
        if outside:
            raise ValueError("Changes outside approved publication scope: " + ", ".join(outside))
        return sorted(stage_paths)

    def _proof(self, folder, kind, task_id, payload):
        path = folder / (kind + ".json")
        with path.open("x", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        Journal(self.store).append({"kind": kind, "task_id": task_id,
            "summary": ("Published" if kind == "published" else "Created local") + " commit " + payload["commit"],
            "evidence_refs": [str(path) + "#sha256=" + digest, "git:commit:" + payload["commit"]]},
            event_key="publication:" + folder.name + ":" + kind)
        return {"path": str(path), "sha256": digest}

    def publish(self, task_id, execute_push=False, remote="origin", commit_message=None):
        remote = self._remote(remote)
        if type(execute_push) is not bool:
            raise ValueError("execute_push must be boolean")
        if commit_message is not None:
            text(commit_message, "commit message", limit=2000)
        task = self.tasks.snapshot(task_id)
        if task["legacy_v1"]:
            return self._failure("BLOCKED", "Legacy tasks have no v2 publishing authority")
        if task["contract"]["profile"] != "software":
            return self._failure("BLOCKED", "Publishing requires the software acceptance profile")
        if task["state"] == "CANCELLED":
            return self._failure("BLOCKED", "Cancelled task cannot publish")
        self._repository()
        root_id = WorkflowController(self.store)._root_task(task_id)
        with _exclusive(self.store.root / "tasks" / root_id / "execution.lock"):
            return self._publish(task_id, execute_push, remote, commit_message)

    def _publish(self, task_id, execute_push, remote, commit_message):
        task = self.tasks.snapshot(task_id)
        gate = GateService(self.store).gate(task_id)
        if gate["status"] != "APPROVED":
            return {**gate, "published": False}
        branch = self._git("branch", "--show-current")
        if not re.fullmatch(r"(?:feature|bugfix)/[A-Za-z0-9][A-Za-z0-9._/-]*", branch):
            return self._failure("REJECTED", "Publishing requires the feature/* or bugfix/* branch allowlist")
        dirty = self._git("status", "--porcelain")
        if dirty and not commit_message:
            return self._failure("BLOCKED", "Working tree dirty; supply a reviewed commit message or commit intended changes first")
        baseline = read_record(self.store.db, "baseline", task_id)["payload"]
        if not isinstance(baseline, dict) or not isinstance(baseline.get("files"), dict):
            return self._failure("BLOCKED", "Task source baseline is unavailable")
        if dirty and (not isinstance(baseline.get("dirty"), str) or baseline["dirty"]):
            return self._failure("REJECTED", "Preexisting or unknown baseline changes prevent automatic staging ownership")
        current = source_snapshot(self.store, task["contract"], task_id=task_id)
        try:
            stage_paths = self._publication_paths(task, baseline, current)
            if dirty and not stage_paths:
                raise ValueError("Dirty work has no exact approved staging paths")
        except ValueError as ex:
            return self._failure("REJECTED", str(ex))
        try:
            urls = self._urls(remote)
        except ValueError as ex:
            return self._failure("REJECTED", str(ex))
        authorized = self._authorized(remote, urls[0])
        stage_arguments = [":(literal)" + path for path in stage_paths]
        commands = ([["git", "add", "-A", "--", *stage_arguments], ["git", "commit", "-m", commit_message]] if dirty else [])
        commands.append(["git", "push", "--porcelain", "--no-follow-tags", remote, "HEAD:refs/heads/" + branch])
        plan = {"status": "APPROVED", "published": False, "dry_run": not execute_push,
                "remote": remote, "branch": branch, "authorized": authorized, "commands": commands}
        if not execute_push:
            return plan
        if not authorized:
            return {**plan, "status": "BLOCKED", "reasons": ["Explicit exact push URL authorization missing or changed"]}
        secret = self._secret_check(baseline, current)
        if secret:
            return self._failure("REJECTED", secret)
        if current["fingerprint"] != gate.get("fingerprint"):
            return self._failure("REJECTED", "Source changed before publishing")
        folder = self.store.root / "tasks" / task_id / "publications" / uuid.uuid4().hex
        _safe_state(folder)
        folder.mkdir(parents=True)
        proof = {"task_id": task_id, "project_id": self.store.project_id, "workspace_id": self.store.workspace_id,
                 "remote": remote, "url": urls[0], "branch": branch, "fingerprint": current["fingerprint"], "commands": []}

        def run(*args):
            result = execute(self._argv(*args), self.store.repo, folder / (str(len(proof["commands"])) + ".log"), 300,
                             env=self._environment())
            proof["commands"].append(result)
            return result

        if dirty:
            for args in (("add", "-A", "--", *stage_arguments), ("diff", "--cached", "--check"), ("commit", "-m", commit_message)):
                result = run(*args)
                if result.get("exit_code") != 0 or result.get("error") or result.get("timed_out"):
                    return self._failure("BLOCKED", "Git " + args[0] + " failed; inspect actual command logs", evidence_root=str(folder))
            proof["commit"] = self._git("rev-parse", "HEAD")
            self._proof(folder, "committed", task_id, proof)
        commit = self._git("rev-parse", "HEAD")
        proof["commit"] = commit
        if self._git("status", "--porcelain") or source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"] != gate["fingerprint"]:
            return self._failure("REJECTED", "Commit/hooks changed tested source or left dirty worktree", commit=commit)
        try:
            self._publication_paths(task, baseline, source_snapshot(self.store, task["contract"], task_id=task_id))
        except ValueError as ex:
            return self._failure("REJECTED", str(ex), commit=commit)
        # Recompute through the real gate after commit hooks and immediately
        # before push; historical APPROVED JSON is never sufficient.
        fresh = GateService(self.store).gate(task_id)
        if fresh["status"] != "APPROVED":
            return {**fresh, "published": False, "commit": commit}
        if any(fresh.get(key) != gate.get(key) for key in ("fingerprint", "contract_hash", "proposal_hash", "plan_hash")):
            return self._failure("REJECTED", "Reviewed inputs changed before publishing", commit=commit)
        try:
            final_urls = self._urls(remote)
        except ValueError as ex:
            return self._failure("REJECTED", str(ex), commit=commit)
        if final_urls != urls or not self._authorized(remote, urls[0]):
            return self._failure("REJECTED", "Push destination or authorization changed during commit/hooks", commit=commit)
        if self._git("branch", "--show-current") != branch or self._git("rev-parse", "HEAD") != commit:
            return self._failure("REJECTED", "Branch or commit changed during commit/hooks", commit=commit)
        if self._git("status", "--porcelain") or source_snapshot(self.store, task["contract"], task_id=task_id)["fingerprint"] != fresh["fingerprint"]:
            return self._failure("REJECTED", "Source changed immediately before push", commit=commit)
        # Pin the exact tested commit, so a pre-push hook cannot substitute a
        # different HEAD. Hooks still run normally and may reject the operation.
        pushed = run("push", "--porcelain", "--no-follow-tags", remote, commit + ":refs/heads/" + branch)
        observed = run("ls-remote", "--heads", urls[0], "refs/heads/" + branch)
        remote_commit = None
        if observed.get("exit_code") == 0 and not observed.get("error") and not observed.get("timed_out"):
            for line in Path(observed["log"]).read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[1] == "refs/heads/" + branch:
                    remote_commit = fields[0]
        if remote_commit != commit:
            return self._failure("BLOCKED", "Push not confirmed at the authorized destination; inspect actual command evidence before retrying",
                                 commit=commit, publication_state="UNKNOWN" if observed.get("exit_code") != 0 else "NOT_CONFIRMED", evidence_root=str(folder))
        proof.update(remote_commit=remote_commit, reconciled_push_error=pushed.get("exit_code") != 0,
                     confirmed_at_utc=now())
        artifact = self._proof(folder, "published", task_id, proof)
        return {**plan, "published": True, "commit": commit, "remote_commit": remote_commit,
                "reconciled_push_error": proof["reconciled_push_error"], "proof": artifact}
