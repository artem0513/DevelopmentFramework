"""Read-only Git inspection for local CLI workflow services.

Git's apparently read-only commands can invoke repository-configured fsmonitor,
clean filters and diff helpers. This adapter disables those execution paths and
accepts only the inspection forms used by the local runtime. Publishing
continues to use the ordinary CLI inside its existing authorization boundary.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess


def _trusted_git():
    """Resolve once from absolute host PATH entries, never the current directory."""
    executable = "git.exe" if os.name == "nt" else "git"
    current = Path.cwd().resolve()
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        directory = Path(os.path.expandvars(entry.strip('"')))
        if not directory.is_absolute():
            continue
        directory = directory.resolve()
        if directory == current:
            continue
        candidate = directory / executable
        if candidate.is_file() and (os.name == "nt" or os.access(candidate, os.X_OK)):
            return str(candidate.resolve(strict=True))
    raise ValueError("Trusted Git executable unavailable in absolute host PATH")


try:
    TRUSTED_GIT = _trusted_git()
except ValueError:
    # Generic project memory, documents and reporting do not require Git.
    # Resolve/raise only when an actual software inspection requests it.
    TRUSTED_GIT = None


def trusted_git():
    return TRUSTED_GIT or _trusted_git()


def _environment():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update({"GIT_CONFIG_SYSTEM": os.devnull, "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_ALLOW_PROTOCOL": "",
                "GIT_NO_LAZY_FETCH": "1", "GIT_TERMINAL_PROMPT": "0"})
    return env


def _base(repo):
    return [trusted_git(), "--no-pager", "--no-optional-locks", "-C", str(repo),
            # Trust only the already selected canonical checkout for this invocation.
            # Windows tool sandboxes run as a different user; helpers remain disabled.
            "-c", "safe.directory=" + str(repo), "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-c", "core.worktree=" + str(repo), "-c", "core.attributesFile=" + os.devnull,
            "-c", "diff.external=", "-c", "submodule.recurse=false"]


def _validate(args):
    if not args or not all(isinstance(arg, str) and "\0" not in arg for arg in args):
        raise ValueError("Host Git requires a supported read-only argument list")
    command, *options = args
    if command == "rev-parse" and options in (["HEAD"], ["--show-toplevel"]):
        return
    if command == "branch" and options == ["--show-current"]:
        return
    if command == "status" and options in (["--porcelain"], ["--porcelain=v1"], ["--porcelain=v2"]):
        return
    if command == "ls-files" and set(options).issubset({"--cached", "--others", "--exclude-standard", "-z", "--stage"}):
        return
    if command == "check-ignore" and options == ["--no-index", "--quiet", "--", ".devframework/local/.ase-privacy-probe"]:
        return
    if command == "check-ignore" and options == ["--no-index", "--quiet", "--", ".devframework/local/"]:
        return
    if command == "diff":
        flags = {"--cached", "--check", "--stat", "--name-only", "--no-ext-diff", "--no-textconv"}
        revisions = 0
        for index, option in enumerate(options):
            if option == "--":
                if index != len(options) - 1:
                    raise ValueError("Host Git diff does not accept path operands")
            elif option in flags:
                pass
            elif option == "HEAD" or re.fullmatch(r"[0-9a-fA-F]{40,64}", option):
                revisions += 1
            else:
                raise ValueError("Unsupported host Git diff option")
        if revisions <= 1:
            return
    raise ValueError("Unsupported host Git inspection; mutations and arbitrary options are forbidden")


def _run(repo, args, check=True):
    _validate(args)
    repo = Path(repo).resolve(strict=True)
    env = _environment()
    base = _base(repo)
    # Config inspection itself does not invoke filters. NUL framing preserves
    # unusual subsection names without shell interpolation or line ambiguity.
    filters = subprocess.run([*base, "config", "--null", "--name-only", "--get-regexp", r"^filter\."],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=60, env=env, cwd=Path(__file__).resolve().parent)
    if filters.returncode not in {0, 1}:
        raise ValueError("Could not safely inspect repository filter configuration")
    prefixes = set()
    for key in filters.stdout.decode("utf-8", "strict").split("\0"):
        if key.startswith("filter.") and "." in key[7:]:
            prefix, suffix = key.rsplit(".", 1)
            if suffix.lower() in {"clean", "smudge", "process", "required"}:
                prefixes.add(prefix)
    for prefix in sorted(prefixes):
        for suffix in ("clean", "smudge", "process"):
            base += ["-c", prefix + "." + suffix + "="]
        base += ["-c", prefix + ".required=false"]
    command = list(args)
    if command[0] == "diff":
        command = ["diff", "--no-ext-diff", "--no-textconv", *command[1:]]
    result = subprocess.run([*base, *command], stdin=subprocess.DEVNULL, capture_output=True, timeout=60, env=env,
                            cwd=Path(__file__).resolve().parent)
    if check and result.returncode:
        # Do not echo arbitrary repository-configured strings or credentials to
        # the host API. A failed safe inspection blocks verification.
        raise ValueError("Safe Git " + command[0] + " failed with exit code " + str(result.returncode))
    return result.stdout


def safe_git(repo, *args, check=True):
    return _run(repo, args, check).decode("utf-8", "replace").strip()


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def safe_snapshot(repo, *, _excluded_files=()):
    """The same source-content fingerprint representation as core.snapshot."""
    repo = Path(repo).resolve(strict=True)
    raw = _run(repo, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    paths = sorted(set(raw.decode("utf-8").split("\0")) - {""})
    from .project_local import is_private_path
    if any(is_private_path(path) for path in paths):
        raise ValueError("Private project-local state cannot be source or publication input")
    files = {}
    for relative in paths:
        if relative in _excluded_files:
            continue
        p = repo / relative
        if p.is_file() and p.stat().st_nlink > 1:
            raise ValueError("Hardlinked source cannot be fingerprinted or included in packets")
        if p.resolve().is_relative_to(repo) and is_private_path(p.resolve().relative_to(repo)):
            raise ValueError("Private project-local state cannot be source through an alias")
        if not p.resolve().is_relative_to(repo):
            raise ValueError("Source escapes repository: " + relative)
        if p.is_symlink():
            if not p.is_file():
                raise ValueError("Directory/broken symlink needs explicit verification support: " + relative)
            files[relative] = "link:" + os.readlink(p) + ":" + hashlib.sha256(p.read_bytes()).hexdigest()
        elif p.is_file():
            files[relative] = hashlib.sha256(p.read_bytes()).hexdigest() + (":x" if os.name != "nt" and p.stat().st_mode & 0o111 else "")
        elif p.is_dir():
            raise ValueError("Git submodule/directory source requires separate verified project: " + relative)
    return {"fingerprint": hashlib.sha256(_canonical(files)).hexdigest(), "files": files,
            "commit": safe_git(repo, "rev-parse", "HEAD", check=False) or None,
            "branch": safe_git(repo, "branch", "--show-current"),
            "timestamp": datetime.now(timezone.utc).isoformat()}
