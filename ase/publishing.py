"""Explicit v2 software publication with current evidence and preserved hooks."""
import ast
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tokenize
from urllib.parse import parse_qsl, urlsplit
import uuid

from .core import SECRET, execute, redact
from .gates import GateService, source_snapshot
from .inspection import safe_git, trusted_git
from .journal import Journal
from .native_paths import state_io
from .projects import _safe_state
from .runner import capture_process
from .state import read_record, text, transaction, write_record
from .store import now
from .tasks import TaskService
from .workflow import WorkflowController, _exclusive


class _PublicationHeld(ValueError):
    """Authority rejected before a publication process was launched."""


def _python_has_secret(source):
    """Disambiguate executable bindings while retaining raw literal detection.

    No source is executed. Unknown syntax and payload-bearing expressions keep
    the conservative raw result; lookup keys and regular-expression definitions
    are metadata rather than embedded credential values.
    """
    raw = SECRET.search(source)
    if raw is None:
        return False
    try:
        tree = ast.parse(source)
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (SyntaxError, ValueError, tokenize.TokenError, RecursionError):
        return True
    nodes = list(ast.walk(tree))
    for node in nodes:
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            value = node.value.decode("utf-8", "replace") if isinstance(node.value, bytes) else node.value
            if SECRET.search(value):
                return True
    if any(item.type == tokenize.COMMENT and SECRET.search(item.string) for item in tokens):
        return True
    lines = source.splitlines(keepends=True)
    starts, total = [], 0
    for line in lines:
        starts.append(total)
        total += len(line)

    def position(location):
        line, column = location
        return starts[line - 1] + column

    def ast_start(node):
        # AST columns count UTF-8 bytes; tokenizer columns count characters.
        prefix = lines[node.lineno - 1].encode("utf-8")[:node.col_offset].decode("utf-8")
        return starts[node.lineno - 1] + len(prefix)

    bindings, conditions = {}, set()
    for node in nodes:
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    bindings[ast_start(target)] = node.value
        elif isinstance(node, (ast.If, ast.While)):
            conditions.update(ast_start(item) for item in ast.walk(node.test)
                              if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load))

    def embedded_payload(value):
        if value is None:
            return False
        if isinstance(value, ast.Constant):
            return value.value is not None and type(value.value) is not bool

        def regex_metadata(pattern):
            try:
                re.compile(pattern)
            except (re.error, OverflowError, RecursionError):
                return False
            if not any(marker in pattern for marker in ("[", "\\", "(?")):
                return False
            # Permit pattern syntax and credential-field labels, not an opaque
            # payload merely wrapped in a regex constructor.
            literals = re.sub(r"\[(?:\\.|[^\]\\])*\]", "", pattern)
            literals = re.sub(r"\\.", "", literals)
            fields = {"password", "secret", "bearer", "authorization", "signature", "credential", "username", "api_key", "apikey"}
            return all(part.lower() in fields for part in re.findall(r"[A-Za-z0-9_.-]{6,}", literals))

        metadata = set()
        for item in ast.walk(value):
            if isinstance(item, ast.Subscript) and isinstance(item.slice, ast.Constant):
                metadata.add(item.slice)
            elif isinstance(item, ast.BinOp) and isinstance(item.op, ast.Add):
                for part, other in ((item.left, item.right), (item.right, item.left)):
                    if (isinstance(part, ast.Constant) and isinstance(part.value, str)
                            and len(part.value) <= 2 and not any(character.isalnum() for character in part.value)
                            and any(isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)) for node in ast.walk(other))):
                        metadata.add(part)
            elif isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute):
                owner = item.func.value
                regex = (isinstance(owner, ast.Name) and owner.id == "re" and item.func.attr == "compile"
                         and len(item.args) == 1 and not item.keywords
                         and isinstance(item.args[0], ast.Constant) and isinstance(item.args[0].value, str)
                         and regex_metadata(item.args[0].value))
                environment = ((isinstance(owner, ast.Name) and owner.id == "os" and item.func.attr == "getenv")
                    or (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)
                        and owner.value.id == "os" and owner.attr == "environ" and item.func.attr == "get"))
                if item.args and (regex or environment):
                    metadata.add(item.args[0])
        for item in ast.walk(value):
            if isinstance(item, ast.Constant) and item not in metadata:
                return True
            if isinstance(item, (ast.List, ast.Tuple, ast.Set, ast.Dict)):
                return True
            if (isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
                    and item.func.id in {"str", "bytes", "bytearray", "list", "tuple", "dict", "set", "int", "float", "bool"}
                    and not item.args and not item.keywords):
                return True
        return False

    labels = re.compile(r"(?i)(?:password|api[_-]?key|token|secret)\s*[=:]")
    while raw is not None:
        if labels.match(source, raw.start()) is None:
            return True
        item = next((item for item in tokens if item.type == tokenize.NAME
                     and position(item.start) <= raw.start() < position(item.end)), None)
        if item is None:
            return True
        start = position(item.start)
        if start not in conditions and (start not in bindings or embedded_payload(bindings[start])):
            return True
        # A benign condition may overlap the next literal binding. Never skip
        # that second candidate merely because the first regex consumed it.
        raw = SECRET.search(source, raw.start() + 1)
    return False


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

    def _history_git(self, *args, max_bytes=2_000_000):
        # Read immutable objects without textconv/filters or lazy network fetch.
        # Spooling bounds captured bytes even for a malformed/huge inventory.
        result = capture_process(self._argv(*args), self.store.repo, timeout=60,
            max_output_bytes=max_bytes, env={**self._environment(), "GIT_NO_LAZY_FETCH": "1"})
        if result.get("output_limited"):
            raise ValueError("Git history inspection exceeds its output bound")
        if result.get("exit_code") != 0 or any(result.get(key) for key in ("error", "timed_out", "cancelled")):
            # Never include cat-file output: it may itself be a credential.
            raise ValueError("Git history inspection failed: " + args[0])
        return result["stdout"]

    def _history_secret_check(self, baseline, head=None):
        """Inspect introduced blobs of every unpublished task commit.

        Net tree differences omit reverted/deleted content. Compare every
        commit with each parent (including merge resolutions), then read the
        immutable new blob. Do not exclude blobs merely present at another
        baseline path: copying legacy content introduces it at a new path.
        Earlier baseline history retains its existing task-boundary semantics.
        """
        base = baseline.get("commit")
        if not base:
            return None
        try:
            head = head or self._git("rev-parse", "HEAD")
            if any(not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", value) for value in (base, head)):
                raise ValueError("Full commit identities are required for history inspection")
            self._history_git("merge-base", "--is-ancestor", base, head)
            commits = self._history_git("rev-list", "--max-count=1001", base + ".." + head, max_bytes=80_000).decode("ascii").splitlines()
            if len(commits) > 1000:
                raise ValueError("Publication history exceeds its commit bound")
            blobs, inspected = {}, set()
            total_bytes = total_entries = metadata_bytes = 0
            for commit in commits:
                if not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", commit):
                    raise ValueError("Malformed publication commit identity")
                raw = self._history_git("diff-tree", "--root", "--no-commit-id", "--raw", "--no-abbrev",
                    "--no-renames", "--no-ext-diff", "--no-textconv", "-r", "-m", "-z", commit)
                metadata_bytes += len(raw)
                if metadata_bytes > 8_000_000:
                    raise ValueError("Publication history exceeds its metadata bound")
                rows = raw.split(b"\0")
                if rows.pop() != b"" and raw:
                    raise ValueError("Malformed publication tree inventory")
                if len(rows) % 2:
                    raise ValueError("Malformed publication tree inventory")
                for index in range(0, len(rows), 2):
                    total_entries += 1
                    if total_entries > 10_000:
                        raise ValueError("Publication history exceeds its entry bound")
                    fields = rows[index].split()
                    if (len(fields) != 5 or not fields[0].startswith(b":")
                            or fields[4] not in {b"A", b"M", b"D", b"T"}):
                        raise ValueError("Malformed publication tree entry")
                    mode, oid = fields[1].decode("ascii"), fields[3].decode("ascii")
                    name = rows[index + 1].decode("utf-8", "strict")
                    if not name or not re.fullmatch(r"[a-fA-F0-9]{40}|[a-fA-F0-9]{64}", oid):
                        raise ValueError("Malformed publication blob identity")
                    if fields[4] == b"D":
                        continue  # Its introduced version is inspected in its earlier commit.
                    path = Path(name)
                    if path.name.lower() in {".env", "id_rsa", "id_ed25519"} or path.suffix.lower() in {".pfx", ".p12", ".pem", ".key"}:
                        return "Potential credential file in published history: " + redact(name)
                    if mode not in {"100644", "100755"}:
                        raise ValueError("Unsupported published Git entry mode: " + redact(name))
                    python = path.suffix.lower() in {".py", ".pyw"}
                    if (oid, python) in inspected:
                        continue
                    if oid not in blobs:
                        size = int(self._history_git("cat-file", "-s", oid, max_bytes=1024).decode("ascii"))
                        if size < 0 or size >= 2_000_000 or total_bytes + size > 16_000_000:
                            raise ValueError("Published blob inspection exceeds its byte bound: " + redact(name))
                        data = self._history_git("cat-file", "blob", oid, max_bytes=max(1024, size + 1))
                        if len(data) != size:
                            raise ValueError("Published blob size differs from immutable object metadata")
                        identity = hashlib.new("sha1" if len(oid) == 40 else "sha256",
                            b"blob " + str(size).encode("ascii") + b"\0" + data).hexdigest()
                        if identity != oid.lower():
                            raise ValueError("Published blob content differs from its Git object identity")
                        blobs[oid] = data
                        total_bytes += size
                    data = blobs[oid]
                    if b"\0" in data:
                        raise ValueError("Published binary blob cannot be inspected for secrets: " + redact(name))
                    content = data.decode("utf-8", "strict")
                    detected = _python_has_secret(content) if python else SECRET.search(content)
                    if detected:
                        return "Potential secret in published history: " + redact(name)
                    inspected.add((oid, python))
        except (ValueError, OSError, UnicodeError) as error:
            return "Publication history secret inspection failed: " + redact(error)
        return None

    def _secret_check(self, baseline, current, *, head=None):
        history = self._history_secret_check(baseline, head)
        if history:
            return history
        publishable = set(current["files"])
        if baseline.get("commit"):
            publishable = {path for path in self._git("ls-files", "--cached", "--others", "--exclude-standard", "-z", strip=False).split("\0") if path}
        for name, version in current["files"].items():
            if name not in publishable or baseline["files"].get(name) == version:
                continue
            path = self.store.repo / name
            if path.is_file() and (path.name.lower() in {".env", "id_rsa", "id_ed25519"}
                                  or path.suffix.lower() in {".pfx", ".p12", ".pem", ".key"}):
                return "Potential credential file: " + name
            if path.is_symlink() or (path.is_file() and path.stat().st_nlink > 1):
                return "Changed source is a redirected or shared file: " + name
            if path.is_file() and path.stat().st_size < 2_000_000:
                content = path.read_text(encoding="utf-8", errors="replace")
                detected = _python_has_secret(content) if path.suffix.lower() in {".py", ".pyw"} else SECRET.search(content)
                if detected:
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
        # The fingerprint also observes ignored read-only dependencies. They
        # remain gate inputs, but Git will not stage or publish those files.
        changed = set(stage_paths)
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

    def _publication_authority(self, controller, task_id, remote):
        with transaction(self.store.db):
            task = self.tasks.snapshot(task_id)
            if task["legacy_v1"] or task["contract"]["profile"] != "software":
                raise ValueError("Current software task approval is required for publishing")
            for stage in task["contract"]["stages"]:
                self.tasks.require_approval(task_id, stage["id"], allow_terminal=True)
            if controller._stop_requested(task_id):
                raise ValueError("Task or ancestor is stopped or cancelled")
            authority = controller._authority_binding(task_id)
            return {"workflow": authority,
                "stop_revisions": {item["task_id"]: read_record(self.store.db, "stop", item["task_id"])["revision"]
                    for item in authority["ancestry"]},
                "publishing_authorization_revision": self._authorization(remote)["revision"]}

    def _publication_control(self, controller, task_id, remote, authority):
        def cancelled():
            try:
                return self._publication_authority(controller, task_id, remote) != authority
            except ValueError:
                return True

        @contextmanager
        def launch_guard():
            # Match the workflow's short check/Popen fence. Never keep a DB
            # writer while Git, its hooks or a credential helper is running.
            with transaction(self.store.db):
                if cancelled():
                    raise _PublicationHeld("Original publication authority changed before launch")
                yield
        return cancelled, launch_guard

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
        controller = WorkflowController(self.store)
        try:
            # Capture once before the ownership boundary; a later replacement
            # approval or a stop followed by resume cannot rebind this batch.
            authority = self._publication_authority(controller, task_id, remote)
        except ValueError as error:
            return self._failure("BLOCKED", redact(error))
        root_id = authority["workflow"]["ancestry"][-1]["task_id"]
        with _exclusive(self.store.root / "tasks" / root_id / "execution.lock"):
            cancelled, launch_guard = self._publication_control(controller, task_id, remote, authority)
            if cancelled():
                return self._failure("BLOCKED", "Original publication authority changed before ownership")
            return self._publish(task_id, execute_push, remote, commit_message, cancelled, launch_guard)

    def _publish(self, task_id, execute_push, remote, commit_message, cancelled, launch_guard):
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
            controls = {"cancel_requested": cancelled, "launch_guard": launch_guard} if args[0] in {"add", "commit", "push"} else {}
            result = execute(self._argv(*args), self.store.repo, folder / (str(len(proof["commands"])) + ".log"), 300,
                             env=self._environment(), **controls)
            proof["commands"].append(result)
            return result

        if dirty:
            for args in (("add", "-A", "--", *stage_arguments), ("diff", "--cached", "--check"), ("commit", "-m", commit_message)):
                try:
                    result = run(*args)
                except _PublicationHeld as error:
                    return self._failure("BLOCKED", str(error), evidence_root=str(folder),
                        command_receipts=proof["commands"], observed_head=self._git("rev-parse", "HEAD"))
                if result.get("exit_code") != 0 or result.get("error") or result.get("timed_out"):
                    return self._failure("BLOCKED", "Git " + args[0] + " failed; inspect actual command logs", evidence_root=str(folder),
                        command_receipts=proof["commands"], observed_head=self._git("rev-parse", "HEAD"))
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
        # Hooks may introduce and then remove content while preserving the
        # tested worktree. Inspect the exact commit about to be pushed again.
        secret = self._secret_check(baseline, current, head=commit)
        if secret:
            return self._failure("REJECTED", secret, commit=commit)
        # Pin the exact tested commit, so a pre-push hook cannot substitute a
        # different HEAD. Hooks still run normally and may reject the operation.
        try:
            pushed = run("push", "--porcelain", "--no-follow-tags", remote, commit + ":refs/heads/" + branch)
        except _PublicationHeld as error:
            return self._failure("BLOCKED", str(error), commit=commit, push_state="NOT_LAUNCHED",
                evidence_root=str(folder), command_receipts=proof["commands"])
        # Observation is permitted even after cancellation: an already launched
        # push may have reached the remote and cannot be described as undone.
        observed = run("ls-remote", "--heads", urls[0], "refs/heads/" + branch)
        remote_commit = None
        if observed.get("exit_code") == 0 and not observed.get("error") and not observed.get("timed_out"):
            for line in state_io(observed["log"]).read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) == 2 and fields[1] == "refs/heads/" + branch:
                    remote_commit = fields[0]
        if remote_commit != commit:
            return self._failure("BLOCKED", "Push not confirmed at the authorized destination; inspect actual command evidence before retrying",
                                 commit=commit, publication_state="UNKNOWN" if observed.get("exit_code") != 0 else "NOT_CONFIRMED", evidence_root=str(folder))
        proof.update(remote_commit=remote_commit, reconciled_push_error=pushed.get("exit_code") != 0,
                     confirmed_at_utc=now())
        artifact = self._proof(folder, "published", task_id, proof)
        authority_current = not cancelled()
        return {**plan, "status": plan["status"] if authority_current else "BLOCKED",
                **({} if authority_current else {"reasons": ["Publication confirmed; original authority changed after launch"]}),
                "published": True, "authority_current": authority_current, "commit": commit, "remote_commit": remote_commit,
                "reconciled_push_error": proof["reconciled_push_error"], "proof": artifact}
