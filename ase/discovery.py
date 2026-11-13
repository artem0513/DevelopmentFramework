"""Read scoped project metadata, never execute probes or grant source authority.

Runtime validates attribution and structure. A model/domain tool still has to
interpret sources and decide whether candidate commands and skills fit the task.
"""
import configparser
import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import sys
import tomllib
import xml.etree.ElementTree as ET

from .store import now


_SECRET = re.compile(r"(?i)(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{16,}|Bearer\s+[a-z0-9._-]{12,}|(?:password|api[_-]?key|token|secret)\s*[=:]\s*[^\s,;]{8,}|https?://[^/@\s]+:[^/@\s]+@)")
_INTAKE_LISTS = {"outcomes", "requirements", "constraints", "decisions", "proposals",
                 "contradictions", "dependencies", "success_criteria", "gaps"}
_MARKERS = {"AGENTS.md", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
            "bun.lock", "bun.lockb", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg",
            "requirements.txt", "global.json", "Directory.Build.props", "Directory.Build.targets"}
_MAX_BYTES = 1024 * 1024
_MAX_RESOURCES = 256


def _text(value, name, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or "\0" in value:
        raise ValueError(name + " must be a nonempty bounded string")
    if _SECRET.search(value):
        raise ValueError(name + " must not contain credentials")
    return value


def _list(value, name, limit=200):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(name + " must be a bounded list")
    return value


def _object(value, allowed, name):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Unknown or malformed " + name + " fields")
    return value


def _timestamp(value, name):
    _text(value, name, 80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(name + " must be an ISO timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(name + " requires a timezone")
    return value


def validate_intake(payload):
    """Validate a concise attributed summary; quoted statements are data only.

    Every claim links to a source ID. Meeting sources require a cue/timestamp,
    including a concrete unavailable cue when transcription itself is missing.
    Empty outcomes are allowed only with a recorded gap, so incomplete input is
    representable without silently pretending requirements are known.
    """
    _object(payload, _INTAKE_LISTS | {"source_refs", "authorization_granted", "semantic_validation"}, "intake")
    if payload.get("authorization_granted", False) is not False or payload.get("semantic_validation", False) is not False:
        raise ValueError("Intake cannot grant authorization or semantic validation")
    value = copy.deepcopy(payload)
    sources = _list(value.get("source_refs"), "source_refs")
    if not sources:
        raise ValueError("Intake requires attributed source_refs")
    ids = set()
    for source in sources:
        _object(source, {"id", "kind", "reference", "observed_at", "timestamp", "lines", "status"}, "source")
        for key in ("id", "kind", "reference"):
            _text(source.get(key), "source." + key)
        if source["id"] in ids:
            raise ValueError("Duplicate source ID")
        ids.add(source["id"])
        if source["kind"] not in {"user_message", "document", "meeting", "audio", "transcript", "local", "other"}:
            raise ValueError("Unsupported source kind")
        _timestamp(source.get("observed_at"), "source.observed_at")
        if source["kind"] in {"meeting", "audio", "transcript"} and not source.get("timestamp"):
            raise ValueError("Meeting source requires timestamp/cue attribution")
        for key in ("timestamp", "lines", "status"):
            if key in source:
                _text(source[key], "source." + key, 1000)
    claim_ids = set()
    for key in sorted(_INTAKE_LISTS):
        items = value.setdefault(key, [])
        for item in _list(items, key):
            allowed = {"id", "text", "source_refs", "required", "timestamp", "status"}
            if key in {"gaps", "contradictions"}:
                allowed.add("blocking_stage_ids")
            _object(item, allowed, key)
            _text(item.get("id"), key + ".id", 200)
            _text(item.get("text"), key + ".text")
            if item["id"] in claim_ids:
                raise ValueError("Duplicate intake item ID")
            claim_ids.add(item["id"])
            refs = _list(item.get("source_refs"), key + ".source_refs")
            if not refs or any(not isinstance(ref, str) or ref not in ids for ref in refs):
                raise ValueError("Intake item references unknown source")
            if "required" in item and type(item["required"]) is not bool:
                raise ValueError("required must be boolean")
            if key == "gaps":
                item.setdefault("required", True)
            if "blocking_stage_ids" in item:
                stages = _list(item["blocking_stage_ids"], "blocking_stage_ids")
                if not stages or any(not isinstance(stage, str) for stage in stages) or len(set(stages)) != len(stages):
                    raise ValueError("Gap stage mapping requires distinct nonempty stage IDs")
                for stage in stages:
                    _text(stage, "gap stage", 200)
            for field in ("timestamp", "status"):
                if field in item:
                    _text(item[field], key + "." + field, 1000)
    if not value["outcomes"] and not value["gaps"]:
        raise ValueError("Incomplete intake requires an explicit gap")
    for source in sources:
        if source.get("status") in {"unavailable", "blocked", "partial", "inaudible"}:
            if not any(source["id"] in gap["source_refs"] for gap in value["gaps"]):
                raise ValueError("Incomplete source requires an attributed gap")
    value.update(authorization_granted=False, semantic_validation=False)
    return value


def _scope_path(value):
    from .project_local import is_private_path
    if is_private_path(value):
        raise ValueError("Private project-local state is not a discovery source")
    _text(value, "scope path", 2048)
    path = value.replace("\\", "/")
    if Path(path).is_absolute() or PureWindowsPath(path).is_absolute() or PureWindowsPath(path).drive:
        raise ValueError("Scope paths must be relative to the project root")
    if any(part.lower() in {"..", ".git"} for part in path.split("/")):
        raise ValueError("Scope path escapes the project or names private Git data")
    return Path(path).as_posix()


def _validate_scope(scope):
    _object(scope, {"resources", "dependencies"}, "scope")
    resources = [_scope_path(item) for item in _list(scope.get("resources"), "scope.resources")]
    if not resources:
        raise ValueError("Discovery needs explicitly named resources")
    dependencies = []
    for item in _list(scope.get("dependencies", []), "scope.dependencies"):
        _object(item, {"path", "from", "reason"}, "scope dependency")
        dependency = {"path": _scope_path(item.get("path")), "reason": _text(item.get("reason"), "dependency.reason")}
        if "from" in item:
            dependency["from"] = _scope_path(item["from"])
        dependencies.append(dependency)
    return {"resources": list(dict.fromkeys(resources)), "dependencies": dependencies}


def _project_root(project):
    if isinstance(project, (str, os.PathLike)):
        value = project
    elif isinstance(project, dict):
        value = project.get("root") or project.get("repo")
    else:
        value = getattr(project, "root", None) or getattr(project, "repo", None)
    if value is None:
        raise ValueError("Project must expose a root or repo path")
    root = Path(value).resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Project root must be a directory")
    return root


def _capability_tools(config):
    _object(config, {"tools", "skills"}, "backend_config")
    configured = config.get("tools", {})
    if not isinstance(configured, dict) or len(configured) > 100:
        raise ValueError("tools must be a bounded mapping")
    tools = []
    names = sorted(set(configured) | {"git", "python", "pytest", "node", "npm", "pnpm", "yarn", "bun", "dotnet"})
    for name in names:
        _text(name, "tool name", 100)
        item = configured.get(name, {})
        _object(item, {"executable", "invocation_allowed", "structured_output", "resource_access", "independent_run", "version"}, "tool")
        executable = item.get("executable", sys.executable if name == "python" else name)
        _text(executable, "executable", 2048)
        # Executable presence is an observation, never a version/auth/account probe.
        try:
            found = str(Path(executable).resolve()) if Path(executable).is_file() else shutil.which(executable)
            available = bool(found)
        except OSError:
            found, available = None, "unknown"
        tool = {"name": name, "executable": found, "available": available,
                "version": item.get("version", "unknown"), "version_source": "configuration" if "version" in item else "unprobed",
                "invocation_allowed": item.get("invocation_allowed", "unknown"),
                "structured_output": item.get("structured_output", "unknown"),
                "resource_access": item.get("resource_access", "unknown"),
                "independent_run": item.get("independent_run", "unknown")}
        _text(tool["version"], "tool version", 200)
        for field in ("invocation_allowed", "structured_output", "resource_access", "independent_run"):
            if type(tool[field]) is not bool and tool[field] != "unknown":
                raise ValueError(field + " must be boolean or unknown")
        tools.append(tool)
    skills = copy.deepcopy(_list(config.get("skills", []), "skills"))
    for skill in skills:
        _object(skill, {"name", "path", "version", "scope", "compatible", "selected", "reason"}, "skill metadata")
        _text(skill.get("name"), "skill name", 200)
        for field in ("path", "version", "scope", "reason"):
            if field in skill:
                _text(skill[field], "skill " + field)
        if "compatible" in skill and type(skill["compatible"]) is not bool and skill["compatible"] != "unknown":
            raise ValueError("Skill compatibility must be boolean or unknown")
        if "selected" in skill and type(skill["selected"]) is not bool:
            raise ValueError("Skill selected must be boolean")
        if skill.get("selected") and (skill.get("compatible") is not True or not skill.get("reason")):
            raise ValueError("Selecting a skill requires compatible metadata and a reason")
    return tools, skills


def discover_capabilities(project, *, scope, backend_config):
    """Return candidates based on named files and reasoned local dependencies.

    Directory scopes inspect recognized metadata in that directory; they do not
    walk arbitrary descendants. Ancestor build metadata/instructions and declared
    local package/solution references expand the scope with an explicit reason.
    No subprocess, network, package installation, script import or account scan is
    performed. Unknown permissions and unprobed versions stay unknown.
    """
    root, scope = _project_root(project), _validate_scope(scope)
    tools, skills = _capability_tools(backend_config)
    resources, dependencies, limitations, contents, directories = {}, [], [], {}, set()
    queue = []

    def add(path, reason=None, origin=None):
        from .project_local import is_private_path
        candidate = root / path
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError):
            limitations.append("Unknown dependency path: " + str(path))
            return
        if candidate.is_file() and candidate.stat().st_nlink > 1:
            if reason:
                limitations.append("Hardlinked dependency excluded: " + str(path))
                return
            raise ValueError("Hardlinked files are not discovery sources")
        if (is_private_path(candidate.relative_to(root))
                or (resolved.is_relative_to(root) and is_private_path(resolved.relative_to(root)))):
            if reason:
                limitations.append("Private dependency excluded: " + str(path))
                return
            raise ValueError("Private project-local state is not a discovery source")
        if (not resolved.is_relative_to(root)
                or any(part.lower() == ".git" for part in candidate.relative_to(root).parts)
                or any(part.lower() == ".git" for part in resolved.relative_to(root).parts)):
            if reason:
                limitations.append("Uncovered dependency outside project: " + str(path) + "; " + reason)
                return
            raise ValueError("Scope resource escapes project root")
        relative = candidate.relative_to(root).as_posix()
        if reason:
            dependency = {"path": relative, "reason": reason}
            if origin:
                dependency["from"] = origin
            if dependency not in dependencies:
                dependencies.append(dependency)
        if relative not in resources and relative not in queue:
            queue.append(relative)

    for path in scope["resources"]:
        add(path)
    for item in scope["dependencies"]:
        add(item["path"], item["reason"], item.get("from"))

    def related_metadata(directory, origin):
        try:
            children = list(directory.iterdir())
        except OSError:
            limitations.append("Unable to list metadata in " + directory.relative_to(root).as_posix())
            return
        for child in sorted(children):
            if child.name in _MARKERS or child.suffix.lower() in {".sln", ".slnx", ".csproj", ".fsproj", ".vbproj"}:
                add(child.relative_to(root), "Project configuration/instructions governing " + origin, origin)

    while queue:
        if len(resources) >= _MAX_RESOURCES:
            limitations.append("Discovery resource limit reached; remaining dependencies are uncovered")
            break
        relative = queue.pop(0)
        path = root / relative
        entry = {"path": relative, "status": "UNKNOWN"}
        resources[relative] = entry
        try:
            stat = path.stat()
            if path.is_dir():
                entry.update(status="INSPECTED", type="directory")
                directories.add(path)
                related_metadata(path, relative)
            elif path.is_file():
                entry.update(type="file", size=stat.st_size)
                if stat.st_size > _MAX_BYTES:
                    limitations.append("Resource exceeds read limit: " + relative)
                    continue
                data = path.read_bytes()
                entry.update(status="INSPECTED", sha256=hashlib.sha256(data).hexdigest())
                try:
                    contents[relative] = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    limitations.append("Binary resource was fingerprinted, not interpreted: " + relative)
            else:
                limitations.append("Unsupported resource type: " + relative)
        except FileNotFoundError:
            entry["status"] = "MISSING"
            limitations.append("Missing scoped resource: " + relative)
            continue
        except OSError:
            limitations.append("Unable to read scoped resource: " + relative)
            continue
        # Read only recognized metadata in a containing directory, never sibling
        # source contents. Each parent step is traceable to its original resource.
        parent = path if path.is_dir() else path.parent
        while parent.is_relative_to(root):
            if parent not in directories:
                directories.add(parent)
                related_metadata(parent, relative)
            if parent == root:
                break
            parent = parent.parent
        text = contents.get(relative)
        if text is None:
            continue
        try:
            if path.name == "package.json":
                package = json.loads(text)
                for group in ("dependencies", "devDependencies", "optionalDependencies"):
                    for name, version in package.get(group, {}).items():
                        if isinstance(version, str) and version.startswith(("file:", "link:")):
                            target = (path.parent / version.split(":", 1)[1]).resolve()
                            if target.is_relative_to(root):
                                add(target.relative_to(root), "Local package dependency " + name, relative)
                            else:
                                limitations.append("Uncovered local package dependency outside project: " + name)
            elif path.suffix.lower() in {".sln", ".slnx", ".csproj", ".fsproj", ".vbproj"}:
                references = re.findall(r'"([^"\n]+\.(?:csproj|fsproj|vbproj))"', text, re.I) if path.suffix.lower() == ".sln" else [element.attrib.get("Path") or element.attrib.get("Include") for element in ET.fromstring(text).iter() if element.tag.rsplit("}", 1)[-1] in {"Project", "ProjectReference"}]
                for reference in filter(None, references):
                    target = (path.parent / reference.replace("\\", "/")).resolve()
                    if target.is_relative_to(root):
                        add(target.relative_to(root), ".NET project/solution reference", relative)
                    else:
                        limitations.append("Uncovered .NET dependency outside project: " + reference)
        except (ValueError, TypeError, AttributeError, ET.ParseError):
            limitations.append("Malformed configuration; dependency coverage unknown: " + relative)

    checks, stack = [], set()
    def check(identifier, argv, kind, parser, directory, refs, script=False):
        checks.append({"id": identifier, "argv": argv, "kind": kind, "parser": parser,
                       "cwd": directory.relative_to(root).as_posix(), "source_refs": refs,
                       "selected": False, "requires_inspection": script})

    for relative, text in contents.items():
        path = root / relative
        if path.name == "package.json":
            try:
                package = json.loads(text)
                declared = package.get("packageManager", "")
                manager = declared.split("@", 1)[0] if isinstance(declared, str) else ""
                if manager not in {"npm", "pnpm", "yarn", "bun"}:
                    present = [manager for marker, manager in (("bun.lock", "bun"), ("bun.lockb", "bun"), ("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm")) if (path.parent / marker).as_posix() in {p.as_posix() for p in (root / name for name in resources)}]
                    unique = set(present)
                    if len(unique) > 1 or declared:
                        limitations.append("Ambiguous or unsupported package manager in " + relative)
                        continue
                    manager = present[0] if present else "npm"
                for script in package.get("scripts", {}):
                    if script in {"build", "lint", "typecheck", "test", "test:unit", "test:integration"}:
                        kind = "test" if script.startswith("test") else "build" if script == "build" else "lint"
                        check("node-" + str(len(checks) + 1), [manager, "run", script], kind, "node" if kind == "test" else None, path.parent, [relative], True)
                stack.add("node")
            except (ValueError, TypeError, AttributeError):
                limitations.append("Malformed package configuration: " + relative)
        elif path.suffix.lower() in {".sln", ".slnx"}:
            stack.add("dotnet")
            for verb in ("build", "test"):
                check("dotnet-" + str(len(checks) + 1), ["dotnet", verb, path.name], "test" if verb == "test" else "build", "dotnet" if verb == "test" else None, path.parent, [relative], True)

    solutions = [name for name in resources if Path(name).suffix.lower() in {".sln", ".slnx"}]
    if len(solutions) > 1:
        limitations.append("Multiple .NET solutions found; all are candidates, none selected")
    if not solutions:
        for relative in resources:
            path = root / relative
            if path.suffix.lower() in {".csproj", ".fsproj", ".vbproj"}:
                stack.add("dotnet")
                for verb in ("build", "test"):
                    check("dotnet-" + str(len(checks) + 1), ["dotnet", verb, path.name], "test" if verb == "test" else "build", "dotnet" if verb == "test" else None, path.parent, [relative], True)

    for directory in sorted(directories):
        pytest, paths, refs, malformed = False, [], [], False
        for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg", "requirements.txt"):
            relative = (directory / name).relative_to(root).as_posix()
            if relative not in contents:
                if resources.get(relative, {}).get("status") == "UNKNOWN":
                    malformed = True
                    limitations.append("Python test convention is unknown because metadata is unreadable: " + relative)
                continue
            text = contents[relative]
            try:
                if name == "pyproject.toml":
                    data = tomllib.loads(text)
                    settings = data.get("tool", {}).get("pytest", {}).get("ini_options")
                    dependencies_text = json.dumps(data.get("project", {}))
                    if settings is not None or re.search(r'\bpytest\b', dependencies_text):
                        pytest, refs = True, refs + [relative]
                        if settings is not None:
                            paths = settings.get("testpaths", [])
                elif name in {"pytest.ini", "tox.ini", "setup.cfg"}:
                    data = configparser.ConfigParser(interpolation=None)
                    data.read_string(text)
                    section = "tool:pytest" if name == "setup.cfg" else "pytest"
                    if data.has_section(section):
                        pytest, refs = True, refs + [relative]
                        paths = data.get(section, "testpaths", fallback="").split()
                elif re.search(r"(?im)^\s*pytest(?:[\s<=>\[!~]|$)", text):
                    pytest, refs = True, refs + [relative]
            except (ValueError, TypeError, AttributeError, configparser.Error):
                malformed = True
                limitations.append("Malformed Python test configuration: " + relative)
        if malformed:
            continue
        if pytest:
            if isinstance(paths, str):
                paths = paths.split()
            if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
                limitations.append("Unknown pytest testpaths in " + directory.relative_to(root).as_posix())
                continue
            stack.add("python")
            check("pytest-" + str(len(checks) + 1), [sys.executable, "-m", "pytest", *paths], "test", "pytest", directory, refs, True)
        elif not malformed:
            tests = directory / "tests"
            try:
                has_tests = tests.is_dir() and any(p.name.startswith("test") and p.suffix == ".py" for p in tests.iterdir())
            except OSError:
                has_tests = False
                limitations.append("Unable to inspect test file names in " + tests.relative_to(root).as_posix())
            if has_tests:
                stack.add("python")
                check("unittest-" + str(len(checks) + 1), [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], "test", "unittest", directory, [tests.relative_to(root).as_posix()], True)
    limitations.append("Candidates require task-specific inspection and approval; presence does not prove permissions, account access or independent execution")
    return {"root": str(root), "generated_at": now(), "git_repository": (root / ".git").exists(),
            "scope": scope, "resources": list(resources.values()), "dependencies": dependencies,
            "candidate_checks": checks, "stack": sorted(stack), "tools": tools, "skills": skills,
            "selected_tools": [], "selected_skills": [s["name"] for s in skills if s.get("selected")],
            "limitations": list(dict.fromkeys(limitations)), "authorization_granted": False, "semantic_validation": False}


def validate_discovery(payload):
    """Validate persistable discovery structure, without interpreting its claims."""
    allowed = {"root", "generated_at", "git_repository", "scope", "resources", "dependencies", "candidate_checks", "stack", "tools", "skills", "selected_tools", "selected_skills", "limitations", "intake", "authorization_granted", "semantic_validation"}
    _object(payload, allowed, "discovery")
    if payload.get("authorization_granted", False) is not False or payload.get("semantic_validation", False) is not False:
        raise ValueError("Discovery cannot grant authorization or semantic validation")
    value = copy.deepcopy(payload)
    value["scope"] = _validate_scope(value.get("scope"))
    if "root" in value:
        _text(value["root"], "root", 2048)
    if "generated_at" in value:
        _timestamp(value["generated_at"], "generated_at")
    if "git_repository" in value and type(value["git_repository"]) is not bool:
        raise ValueError("git_repository must be boolean")
    resource_paths = set()
    for item in _list(value.get("resources"), "resources", _MAX_RESOURCES):
        _object(item, {"path", "status", "type", "size", "sha256"}, "resource")
        path = _scope_path(item.get("path"))
        if path in resource_paths:
            raise ValueError("Duplicate resource path")
        resource_paths.add(path)
        if item.get("status") not in {"INSPECTED", "UNKNOWN", "MISSING"}:
            raise ValueError("Invalid resource status")
        if "size" in item and (type(item["size"]) is not int or item["size"] < 0):
            raise ValueError("Invalid resource size")
        if "sha256" in item and not re.fullmatch(r"[a-f0-9]{64}", str(item["sha256"])):
            raise ValueError("Invalid resource hash")
    for item in _list(value.get("dependencies"), "dependencies", 2000):
        _object(item, {"path", "from", "reason"}, "dependency")
        _scope_path(item.get("path"))
        _text(item.get("reason"), "dependency.reason")
        if "from" in item:
            _scope_path(item["from"])
    check_ids = set()
    for item in _list(value.get("candidate_checks"), "candidate_checks"):
        _object(item, {"id", "argv", "kind", "parser", "cwd", "source_refs", "selected", "requires_inspection"}, "candidate check")
        _text(item.get("id"), "check.id", 200)
        if item["id"] in check_ids:
            raise ValueError("Duplicate check ID")
        check_ids.add(item["id"])
        argv = _list(item.get("argv"), "check.argv")
        if not argv:
            raise ValueError("Candidate requires argv")
        for argument in argv:
            _text(argument, "check argument")
        if item.get("kind") not in {"test", "build", "lint", "security", "smoke"}:
            raise ValueError("Invalid check kind")
        if item.get("parser") not in {None, "pytest", "unittest", "node", "dotnet"}:
            raise ValueError("Invalid check parser")
        _scope_path(item.get("cwd"))
        refs = _list(item.get("source_refs"), "check.source_refs")
        if not refs:
            raise ValueError("Candidate checks require source references")
        for ref in refs:
            _scope_path(ref)
        for field in ("selected", "requires_inspection"):
            if type(item.get(field)) is not bool:
                raise ValueError(field + " must be boolean")
    for key in ("selected_tools", "selected_skills", "limitations", "stack"):
        for item in _list(value.setdefault(key, []), key):
            _text(item, key)
    # Tool and skill metadata were observed/reported, never executed by record.
    tool_names = set()
    for tool in _list(value.setdefault("tools", []), "tools"):
        _object(tool, {"name", "executable", "available", "version", "version_source", "invocation_allowed", "structured_output", "resource_access", "independent_run"}, "observed tool")
        name = _text(tool.get("name"), "tool name", 100)
        if name in tool_names:
            raise ValueError("Duplicate tool name")
        tool_names.add(name)
        for field in ("available", "invocation_allowed", "structured_output", "resource_access", "independent_run"):
            if type(tool.get(field)) is not bool and tool.get(field) != "unknown":
                raise ValueError(field + " must be boolean or unknown")
        for field in ("version", "version_source"):
            _text(tool.get(field), "tool " + field, 200)
        if tool.get("executable") is not None:
            _text(tool["executable"], "tool executable", 2048)
    skill_names = set()
    for skill in _list(value.setdefault("skills", []), "skills"):
        _object(skill, {"name", "path", "version", "scope", "compatible", "selected", "reason"}, "skill metadata")
        name = _text(skill.get("name"), "skill name", 200)
        if name in skill_names:
            raise ValueError("Duplicate skill name")
        skill_names.add(name)
        for field in ("path", "version", "scope", "reason"):
            if field in skill:
                _text(skill[field], "skill " + field)
        if type(skill.get("compatible", "unknown")) is not bool and skill.get("compatible", "unknown") != "unknown":
            raise ValueError("Skill compatible must be boolean or unknown")
        if "selected" in skill and type(skill["selected"]) is not bool:
            raise ValueError("Skill selected must be boolean")
        if name in value["selected_skills"] or skill.get("selected"):
            if skill.get("compatible") is not True or not skill.get("reason"):
                raise ValueError("Selecting a skill requires compatibility and a reason")
    if not set(value["selected_tools"]).issubset(tool_names) or not set(value["selected_skills"]).issubset(skill_names):
        raise ValueError("Selected capabilities must exist in the manifest")
    if "intake" in value:
        value["intake"] = validate_intake(value["intake"])
    value.update(authorization_granted=False, semantic_validation=False)
    # Bound and credential-check the complete serialized payload, including
    # optional metadata fields, before any transaction is opened.
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 2 * 1024 * 1024 or _SECRET.search(encoded):
        raise ValueError("Discovery must be concise and contain no credentials")
    return value


def record_discovery(store, task_id, payload, *, expected_revision):
    """Append project-local discovery using its own monotonic CAS revision."""
    _text(task_id, "task_id", 200)
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    value = validate_discovery(payload)
    db = store.db
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT 1 FROM tasks WHERE id=?", (task_id,)).fetchone() is None:
            raise ValueError("Unknown task in this project: " + task_id)
        previous = db.execute("SELECT payload FROM task_discoveries WHERE task_id=? ORDER BY revision DESC LIMIT 1", (task_id,)).fetchone()
        previous_intake = json.loads(previous[0]).get("intake") if previous else None
        if previous_intake:
            if "intake" not in value:
                value["intake"] = copy.deepcopy(previous_intake)
            else:
                newer = value["intake"]
                updated_gaps = {item["id"]: item for item in [*newer.get("gaps", []), *newer.get("contradictions", [])]}
                old_sources = {source["id"]: source for source in previous_intake["source_refs"]}
                new_sources = {source["id"]: source for source in newer["source_refs"]}
                for gap in [*previous_intake.get("gaps", []), *previous_intake.get("contradictions", [])]:
                    if not gap.get("required", True) or gap.get("status", "UNRESOLVED").upper() in {"RESOLVED", "CONFIRMED"}:
                        continue
                    replacement = updated_gaps.get(gap["id"])
                    if replacement is None:
                        raise ValueError("An open required source gap cannot be dropped: " + gap["id"])
                    if not replacement.get("required", True):
                        raise ValueError("A required source gap needs explicit resolution: " + gap["id"])
                    resolved = replacement.get("status", "UNRESOLVED").upper() in {"RESOLVED", "CONFIRMED"}
                    old_mapping, new_mapping = gap.get("blocking_stage_ids"), replacement.get("blocking_stage_ids")
                    narrowed = new_mapping is not None and (old_mapping is None or not set(old_mapping).issubset(new_mapping))
                    evidence_changed = any(source_id not in old_sources or any(
                        old_sources[source_id].get(field) != new_sources[source_id].get(field)
                        for field in ("kind", "reference", "timestamp", "lines", "status"))
                        for source_id in replacement["source_refs"])
                    if (resolved or narrowed) and not evidence_changed:
                        raise ValueError("Resolving or narrowing a source gap requires new attributed evidence: " + gap["id"])
        from .tasks import TaskService
        intake = value.get("intake", {})
        mapped_gaps = [gap for gap in [*intake.get("gaps", []), *intake.get("contradictions", [])]
                       if "blocking_stage_ids" in gap]
        contract = TaskService(store).snapshot(task_id)["contract"] if mapped_gaps else {}
        stages = contract.get("stages", [])
        stage_ids = {stage["id"] for stage in stages}
        for gap in mapped_gaps:
            mapped = set(gap["blocking_stage_ids"])
            if not mapped.issubset(stage_ids):
                raise ValueError("Gap mapping references an unknown stage")
            unknown = next((u for u in contract.get("unknowns", []) if u["id"] == gap["id"]), None)
            if unknown:
                declared = set(unknown.get("blocking_stage_ids", [])) | {
                    stage["id"] for stage in stages if gap["id"] in stage.get("unknown_ids", [])}
                if mapped != declared:
                    raise ValueError("Gap stage mapping conflicts with contract obligations")
        current = db.execute("SELECT COALESCE(MAX(revision),0) FROM task_discoveries WHERE task_id=?", (task_id,)).fetchone()[0]
        if current != expected_revision:
            raise ValueError("Stale discovery revision; reload and reconcile before saving")
        created, revision = now(), current + 1
        db.execute("INSERT INTO task_discoveries(task_id,revision,payload,created) VALUES(?,?,?,?)", (task_id, revision, json.dumps(value, ensure_ascii=False), created))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return {"task_id": task_id, "revision": revision, "payload": value, "created": created}
